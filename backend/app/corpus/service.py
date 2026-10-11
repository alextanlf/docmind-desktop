"""把抓取放进后台任务。

为什么必须后台：同步抓取的上限不是"站点有多少页"，而是"用户愿意等多久"。于是页数预算
只能给到 20 页 —— 对文档站来说那常常只是开头。改成后台任务之后，预算由**抓取策略**
决定（默认 200 页，与批量导入的网页发现同一上限），用户可以去做别的事。

三个状态全部落库（见 `WebSourceRecord.crawl_state`）：进行中的进度、上一次的结果、
以及"被中断"。所以刷新页面、切走再回来、甚至重启应用之后，界面上的东西都不会说谎。

并发：**同一个域名不允许两个抓取任务**（重复点击不该起第二个），不同域名允许并行 ——
礼貌策略本来就是按主机算的，而用户一次点两个站是明确意图。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.api.errors import DomainError
from app.corpus.crawl import CorpusCrawler, CrawlSummary
from app.document.reachability import rejection_reason
from app.storage.repositories import WebSourceStore

logger = logging.getLogger(__name__)

#: 后台抓取的默认页数预算。与批量导入的网页发现一致（都是 200），但意义不同：
#: 那边的 200 是"一次导入能承受多少"，这里的 200 是"后台任务的一次自然后果"。
DEFAULT_PAGE_BUDGET = 200


class CorpusCrawlService:
    def __init__(
        self,
        *,
        store: WebSourceStore,
        crawler: CorpusCrawler,
        page_budget: int = DEFAULT_PAGE_BUDGET,
    ) -> None:
        self.store = store
        self.crawler = crawler
        self.page_budget = page_budget
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def running(self, domain: str) -> bool:
        task = self._tasks.get(domain)
        return task is not None and not task.done()

    async def start(self, domain: str, *, entry_url: str | None) -> bool:
        """起一个抓取任务。该域名已经在抓时返回 False。"""
        if self.running(domain):
            return False
        if not self.store.mark_crawl_started(domain, page_budget=self.page_budget):
            return False
        task = asyncio.create_task(self._run(domain, entry_url))
        self._tasks[domain] = task
        task.add_done_callback(lambda _done, key=domain: self._tasks.pop(key, None))
        return True

    async def _run(self, domain: str, entry_url: str | None) -> None:
        try:
            summary = await self.crawler.crawl(
                domain=domain,
                entry_url=entry_url,
                max_pages=self.page_budget,
                on_progress=lambda pages, updated: self.store.mark_crawl_progress(
                    domain, pages=pages, updated=updated
                ),
            )
        except asyncio.CancelledError:
            # 取消不是失败，但也不是"跑完了"。
            self.store.mark_crawl_finished(domain, error=None, summary={}, interrupted=True)
            raise
        except DomainError as error:
            # 抓取内部是按页捕获失败的，能逃到这里的是整条抓取起不来的情况（入口地址
            # 非法、越界……）。同样记**原因码**，与逐页失败用同一套取值。
            logger.warning("corpus crawl failed for %s: %s", domain, error.message)
            self.store.mark_crawl_finished(
                domain,
                error=rejection_reason(
                    error.code, upstream_status=getattr(error, "upstream_status", None)
                ),
                summary={},
            )
        except Exception:
            # 后台任务的异常没有人接收：不在这里落状态，界面上就会永远停在"正在抓取"。
            # 记成 interrupted 而不是 idle —— 程序缺陷不该看起来像"这个站没有内容"。
            logger.exception("corpus crawl crashed for %s", domain)
            self.store.mark_crawl_finished(domain, error=None, summary={}, interrupted=True)
        else:
            self.store.mark_crawl_finished(
                domain,
                error=summary.dominant_failure(),
                summary=_summary_payload(summary),
            )

    def recover_on_startup(self) -> int:
        return self.store.recover_interrupted_crawls()


def _summary_payload(summary: CrawlSummary) -> dict[str, Any]:
    return {
        "pages": summary.pages,
        "chunks": summary.chunks,
        "keptPages": summary.kept_pages,
        "updatedPages": summary.updated_pages,
        "removedPages": summary.removed_pages,
        "skippedCount": len(summary.skipped),
        "truncated": summary.truncated,
    }
