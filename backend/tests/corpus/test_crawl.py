"""按需抓取：预算、范围、失败原因、以及"整体替换"的语义。"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.corpus.crawl import CorpusCrawler
from app.document.chunker import SemanticChunker
from app.document.parser import DocumentParser
from app.document.politeness import DomainRateLimiter
from app.document.reachability import ReachabilityProbe
from app.document.safe_http import SafeHttpClient
from app.storage.database import Database
from app.storage.repositories import WebCorpusStore


class Resolver:
    async def resolve(self, host: str):
        return ["93.184.216.34"]


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def now(self) -> float:
        return self.value


class Sleeper:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock

    async def __call__(self, seconds: float) -> None:
        self.clock.value += seconds


PAGE = (
    b"<html><head><title>Guide</title></head><body>"
    b"<h1>Guide</h1><p>DocMind guide body with enough words to become a chunk. "
    b"asyncio event loop and tasks are discussed here in some length.</p>"
    b"<a href='/guide/a'>a</a><a href='/outside'>outside</a>"
    b"</body></html>"
)
CHILD = b"# Child\n\nMarkdown child page content, also long enough to chunk."


def routes(**overrides):
    base = {
        "/robots.txt": (200, "text/plain", b"User-agent: *\nAllow: /"),
        "/guide": (200, "text/html", PAGE),
        "/guide/a": (200, "text/markdown", CHILD),
    }
    base.update(overrides)
    return base


def build_crawler(tmp_path: Path, table: dict, *, probe_handler=None, limiter=None):
    def handler(request: httpx.Request) -> httpx.Response:
        outcome = table.get(request.url.path)
        if outcome is None:
            return httpx.Response(404, headers={"content-type": "text/plain"}, content=b"", request=request)
        if isinstance(outcome, Exception):
            raise outcome
        status, media, body = outcome
        return httpx.Response(status, headers={"content-type": media}, content=body, request=request)

    database = Database(f"sqlite+pysqlite:///{tmp_path / 'corpus.sqlite3'}")
    database.upgrade()
    store = WebCorpusStore(database)
    crawler = CorpusCrawler(
        client=SafeHttpClient(resolver=Resolver(), transport=httpx.MockTransport(handler)),
        parser=DocumentParser(),
        chunker=SemanticChunker(),
        store=store,
        probe=(
            ReachabilityProbe(resolver=Resolver(), transport=httpx.MockTransport(probe_handler))
            if probe_handler is not None
            else None
        ),
        limiter=limiter,
        max_bytes=1024 * 1024,
    )
    return crawler, store


@pytest.mark.asyncio
async def test_crawl_stores_pages_and_chunks(tmp_path: Path) -> None:
    crawler, store = build_crawler(tmp_path, routes())

    summary = await crawler.crawl(domain="docs.test", entry_url="https://docs.test/guide")

    assert summary.pages == 2
    assert summary.chunks > 0
    stats = store.stats()["docs.test"]
    assert stats.pages == 2
    assert stats.chunks == summary.chunks
    # 语料要能给出处：每条块的 url 就是引用来源。
    assert {chunk.url for chunk in store.chunks()} == {
        "https://docs.test/guide",
        "https://docs.test/guide/a",
    }


@pytest.mark.asyncio
async def test_recrawl_replaces_the_domain_instead_of_accumulating(tmp_path: Path) -> None:
    """页面被删掉、或改版后内容变了，都要自动反映出来 —— 这是"整体替换"的意义。"""
    crawler, store = build_crawler(tmp_path, routes())
    await crawler.crawl(domain="docs.test", entry_url="https://docs.test/guide")

    crawler, store = build_crawler(
        tmp_path,
        routes(**{"/guide/a": (404, "text/plain", b"")}),
    )
    summary = await crawler.crawl(domain="docs.test", entry_url="https://docs.test/guide")

    assert summary.pages == 1
    assert store.stats()["docs.test"].pages == 1
    assert {chunk.url for chunk in store.chunks()} == {"https://docs.test/guide"}


@pytest.mark.asyncio
async def test_page_budget_stops_the_crawl_and_says_so(tmp_path: Path) -> None:
    """抓完整站不是默认行为：按钮触发的动作必须能结束，而"这不是全部"要说出来。"""
    crawler, _ = build_crawler(tmp_path, routes())

    summary = await crawler.crawl(
        domain="docs.test", entry_url="https://docs.test/guide", max_pages=1
    )

    assert summary.pages == 1
    assert summary.truncated is True


@pytest.mark.asyncio
async def test_scope_keeps_the_crawl_inside_the_entry_path(tmp_path: Path) -> None:
    """入口是 /guide，就不该顺手把整站抓下来。"""
    requested: list[str] = []
    table = routes()

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        status, media, body = table[request.url.path]
        return httpx.Response(status, headers={"content-type": media}, content=body, request=request)

    database = Database(f"sqlite+pysqlite:///{tmp_path / 'scope.sqlite3'}")
    database.upgrade()
    crawler = CorpusCrawler(
        client=SafeHttpClient(resolver=Resolver(), transport=httpx.MockTransport(handler)),
        parser=DocumentParser(),
        chunker=SemanticChunker(),
        store=WebCorpusStore(database),
    )

    await crawler.crawl(domain="docs.test", entry_url="https://docs.test/guide")

    assert "/outside" not in requested


@pytest.mark.asyncio
async def test_robots_denied_pages_are_skipped_with_their_reason(tmp_path: Path) -> None:
    crawler, _ = build_crawler(
        tmp_path,
        routes(**{"/robots.txt": (200, "text/plain", b"User-agent: *\nDisallow: /guide/a")}),
    )

    summary = await crawler.crawl(domain="docs.test", entry_url="https://docs.test/guide")

    assert summary.pages == 1
    assert ("https://docs.test/guide/a", "robots_denied") in summary.skipped


@pytest.mark.asyncio
async def test_a_host_that_cannot_be_reached_reports_the_real_reason(tmp_path: Path) -> None:
    """抓不到时用户要知道是哪一类 —— "抓取失败"四种处置完全不同。"""

    def probe_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    crawler, _ = build_crawler(
        tmp_path,
        routes(**{"/guide": httpx.ConnectTimeout("timed out")}),
        probe_handler=probe_handler,
    )

    summary = await crawler.crawl(domain="docs.test", entry_url="https://docs.test/guide")

    assert summary.pages == 0
    assert summary.dominant_failure() == "unreachable"


@pytest.mark.asyncio
async def test_empty_summary_has_no_failure_reason(tmp_path: Path) -> None:
    """全部成功时不该记一个错误；否则界面上会挂着一个不存在的失败。"""
    crawler, _ = build_crawler(tmp_path, routes())

    summary = await crawler.crawl(domain="docs.test", entry_url="https://docs.test/guide")

    assert summary.dominant_failure() is None


@pytest.mark.asyncio
async def test_politeness_applies_between_pages_of_one_source(tmp_path: Path) -> None:
    """同一来源的相邻两页之间要遵守 robots 声明的间隔 —— 抓语料和抓导入是同一批请求，
    共用同一份礼貌策略。"""
    clock = Clock()
    limiter = DomainRateLimiter(clock=clock, sleep=Sleeper(clock))

    class RecordingLimiter(DomainRateLimiter):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.delays: list[float | None] = []

        async def acquire(self, host, *, crawl_delay=None):  # type: ignore[override]
            self.delays.append(crawl_delay)
            return await super().acquire(host, crawl_delay=crawl_delay)

    recording = RecordingLimiter(clock=clock, sleep=Sleeper(clock))
    crawler, _ = build_crawler(
        tmp_path,
        routes(**{"/robots.txt": (200, "text/plain", b"User-agent: *\nCrawl-delay: 3\nAllow: /")}),
        limiter=recording,
    )

    summary = await crawler.crawl(domain="docs.test", entry_url="https://docs.test/guide")

    assert summary.pages == 2
    # 站点声明的间隔被读到并交给了限流器（真正的等待由限流器负责，见 test_politeness）。
    assert 3.0 in recording.delays
    assert limiter is not None
