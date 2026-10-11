"""后台抓取服务：状态机、重复点击、失败落原因码、以及"被中断"的恢复。"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.api.errors import DomainError
from app.corpus.crawl import CrawlSummary
from app.corpus.service import CorpusCrawlService
from app.storage.database import Database
from app.storage.models import CrawlState, WebSourceRecord
from app.storage.repositories import WebSourceStore


class StubCrawler:
    def __init__(self, *, summary=None, error=None, crash=None, gate=None, progress=(0, 0)):
        self.summary = summary
        self.error = error
        self.crash = crash
        self.gate = gate
        self.progress = progress
        self.calls: list[str] = []

    async def crawl(self, *, domain, entry_url, max_pages, max_depth=2, on_progress=None):
        self.calls.append(domain)
        if on_progress is not None and any(self.progress):
            on_progress(*self.progress)
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        if self.crash is not None:
            raise self.crash
        return self.summary


SUMMARY = CrawlSummary(
    domain="docs.test",
    pages=12,
    chunks=48,
    kept_pages=9,
    updated_pages=3,
    removed_pages=1,
    skipped=(("https://docs.test/x", "robots_denied"),),
    truncated=False,
)


def service_for(tmp_path: Path, crawler: StubCrawler) -> tuple[CorpusCrawlService, WebSourceStore]:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'service.sqlite3'}")
    database.upgrade()
    store = WebSourceStore(database)
    store.upsert([WebSourceRecord(domain="docs.test", evidence_json="[]")])
    return CorpusCrawlService(store=store, crawler=crawler, page_budget=50), store


def row(store: WebSourceStore) -> WebSourceRecord:
    return store.list()[0]


@pytest.mark.asyncio
async def test_a_finished_crawl_records_its_outcome_on_the_source(tmp_path: Path) -> None:
    """结果落库而不是只回一次响应：用户刷新页面、下次回来，看到的还应该是它。"""
    service, store = service_for(tmp_path, StubCrawler(summary=SUMMARY))

    assert await service.start("docs.test", entry_url=None) is True
    await asyncio.sleep(0)

    finished = row(store)
    assert finished.crawl_state == CrawlState.IDLE.value
    assert finished.last_error is None
    assert finished.last_crawled_at is not None
    assert "keptPages" in finished.last_summary_json
    # 进度要清空：留着会让界面显示一份早就结束的"进行中"。
    assert finished.crawl_progress_json == "{}"


@pytest.mark.asyncio
async def test_progress_is_written_while_the_crawl_runs(tmp_path: Path) -> None:
    gate = asyncio.Event()
    service, store = service_for(tmp_path, StubCrawler(summary=SUMMARY, gate=gate, progress=(7, 4)))

    await service.start("docs.test", entry_url=None)
    await asyncio.sleep(0)

    running = row(store)
    assert running.crawl_state == CrawlState.RUNNING.value
    assert '"pages": 7' in running.crawl_progress_json
    assert '"budget": 50' in running.crawl_progress_json

    gate.set()
    await asyncio.sleep(0)
    assert row(store).crawl_state == CrawlState.IDLE.value


@pytest.mark.asyncio
async def test_clicking_crawl_twice_does_not_start_two_tasks(tmp_path: Path) -> None:
    gate = asyncio.Event()
    crawler = StubCrawler(summary=SUMMARY, gate=gate)
    service, _ = service_for(tmp_path, crawler)

    assert await service.start("docs.test", entry_url=None) is True
    assert await service.start("docs.test", entry_url=None) is False
    gate.set()
    await asyncio.sleep(0)

    assert crawler.calls == ["docs.test"]


@pytest.mark.asyncio
async def test_a_whole_crawl_failure_is_recorded_as_a_reason_code(tmp_path: Path) -> None:
    service, store = service_for(
        tmp_path,
        StubCrawler(error=DomainError("CRAWL_FETCH_FAILED", "抓不来", 502, False, upstream_status=404)),
    )

    await service.start("docs.test", entry_url=None)
    await asyncio.sleep(0)

    assert row(store).last_error == "http_rejected"
    assert row(store).crawl_state == CrawlState.IDLE.value


@pytest.mark.asyncio
async def test_a_crash_never_leaves_the_source_stuck_on_running(tmp_path: Path) -> None:
    """后台任务的异常没有人接收：不在服务里落状态，界面就会永远停在"正在抓取"。"""
    service, store = service_for(tmp_path, StubCrawler(crash=RuntimeError("boom")))

    await service.start("docs.test", entry_url=None)
    await asyncio.sleep(0)

    # 程序缺陷记成"被中断"，不记成 idle —— 否则一个 bug 看起来像"这个站没有内容"。
    assert row(store).crawl_state == CrawlState.INTERRUPTED.value


@pytest.mark.asyncio
async def test_startup_recovery_turns_running_into_interrupted(tmp_path: Path) -> None:
    """应用退出时还在抓的来源必须改判 —— 否则它会永远显示"正在抓取"，
    而那个任务早已不存在，用户只能干等或删掉重加。"""
    service, store = service_for(tmp_path, StubCrawler(summary=SUMMARY))
    store.mark_crawl_started("docs.test", page_budget=50)
    assert row(store).crawl_state == CrawlState.RUNNING.value

    assert service.recover_on_startup() == 1

    recovered = row(store)
    assert recovered.crawl_state == CrawlState.INTERRUPTED.value
    assert recovered.crawl_progress_json == "{}"
    # 不是"失败"：什么都没出错，只是没跑完。
    assert recovered.last_error is None


@pytest.mark.asyncio
async def test_cancelling_stops_the_task_and_marks_it_interrupted(tmp_path: Path) -> None:
    """取消不是失败：用户该看到"这次没跑完"，而不是一个红色的错误。"""
    gate = asyncio.Event()
    service, store = service_for(tmp_path, StubCrawler(summary=SUMMARY, gate=gate))
    await service.start("docs.test", entry_url=None)
    await asyncio.sleep(0)

    assert service.running("docs.test") is True
    assert service.cancel("docs.test") is True

    for _ in range(10):
        await asyncio.sleep(0)
        if not service.running("docs.test"):
            break

    cancelled = row(store)
    assert cancelled.crawl_state == CrawlState.INTERRUPTED.value
    assert cancelled.last_error is None


@pytest.mark.asyncio
async def test_cancelling_something_that_is_not_running_is_not_an_error(tmp_path: Path) -> None:
    """双击、或者任务刚好在这一刻结束 —— 两者都不需要用户做什么。"""
    service, _ = service_for(tmp_path, StubCrawler(summary=SUMMARY))

    assert service.cancel("docs.test") is False
