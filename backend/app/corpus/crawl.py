"""按需抓取一个来源，产出可检索的语料。

与 `app/document/web_discovery.py` 的关系是**共用零件、不复用流程**：那边服务于
"把一个网站导入成文档"（要用户逐项确认、要落 staging、要建批次条目），而这里的产物
是"一个可检索的语料库"，没有确认环节、按域名整体替换。硬把两者合成一个流程，结果是
两条路都要迁就对方。

复用的零件：`safe_http`（robots、SSRF 闸门、字节上限）、`politeness`（每主机节奏）、
`reachability`（失败原因分类）、`parser`（HTML/PDF → 结构化文本）、`chunker`（切块）。

预算：页数、深度、单页字节三个上限，都是**调用方给**的。抓取一个来源是用户按下按钮
触发的动作，必须能在有限时间内结束 —— 把"抓完整站"当默认行为，用户等到的只会是一个
转很久的圈。
"""

from __future__ import annotations

import hashlib
from collections import Counter, deque
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from app.api.errors import DomainError
from app.document.chunker import SemanticChunker
from app.document.parser import DocumentParser
from app.document.politeness import DomainRateLimiter
from app.document.reachability import Reachability, ReachabilityProbe, rejection_reason
from app.document.safe_http import SafeHttpClient, canonicalize_url, in_scope, path_prefix_for_entry
from app.schemas.batches import CrawlRejectionReason
from app.schemas.imports import DownloadedDocument
from app.storage.models import WebCorpusChunkRecord, WebCorpusPageRecord, new_id
from app.storage.repositories import WebCorpusStore

#: 只收能够解析成正文的类型。与导入路径同一张白名单。
_SUPPORTED_MEDIA = {"text/html", "text/markdown", "application/pdf"}

#: 一次抓取的默认预算。三项都小得"能被用户等到"，因为这是按钮触发的动作。
DEFAULT_MAX_PAGES = 20
DEFAULT_MAX_DEPTH = 2


@dataclass(frozen=True)
class CrawlSummary:
    domain: str
    pages: int
    chunks: int
    #: (url, 原因) —— 每条都是 `CrawlRejectionReason` 里的取值。
    skipped: tuple[tuple[str, str], ...]
    #: 是否因为页数上限提前结束。用户需要知道"这不是全部"。
    truncated: bool

    def dominant_failure(self) -> CrawlRejectionReason | None:
        """全是失败时，最主要的那个原因。没有失败则返回 None。

        返回**原因码**而不是句子：原因码在前端已经有一张文案表（带"该怎么办"），
        存一句自己拼的中文会让同一件事有两处说法，早晚不一致。
        """
        if self.pages:
            return None
        return self.failure_reasons().most_common(1)[0][0] if self.skipped else None

    def failure_reasons(self) -> Counter[str]:
        return Counter(reason for _, reason in self.skipped)


class CorpusCrawler:
    """抓一个来源，替换它的语料。"""

    def __init__(
        self,
        *,
        client: SafeHttpClient,
        parser: DocumentParser,
        chunker: SemanticChunker,
        store: WebCorpusStore,
        probe: ReachabilityProbe | None = None,
        limiter: DomainRateLimiter | None = None,
        max_bytes: int = 20 * 1024 * 1024,
        user_agent: str = "DocMind/2",
    ) -> None:
        self.client = client
        self.parser = parser
        self.chunker = chunker
        self.store = store
        self.probe = probe
        self.limiter = limiter
        self.max_bytes = max_bytes
        self.user_agent = user_agent

    async def crawl(
        self,
        *,
        domain: str,
        entry_url: str | None,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ) -> CrawlSummary:
        entry = canonicalize_url(entry_url or f"https://{domain}/")
        prefix = path_prefix_for_entry(entry)
        queue: deque[tuple[str, int]] = deque([(entry, 0)])
        seen: set[str] = set()
        skipped: list[tuple[str, str]] = []
        pages: list[WebCorpusPageRecord] = []
        chunks: list[WebCorpusChunkRecord] = []
        truncated = False

        while queue:
            if len(pages) >= max_pages:
                truncated = True
                break
            url, depth = queue.popleft()
            url = canonicalize_url(url)
            if url in seen or not in_scope(url, entry, prefix):
                continue
            seen.add(url)

            if not await self.client.robots_allowed(
                url, self.user_agent, scope_origin=entry, scope_prefix=prefix
            ):
                skipped.append((url, "robots_denied"))
                continue

            host = urlsplit(url).hostname or domain
            if not await self._wait_for_turn(host, url):
                skipped.append((url, "limit_reached"))
                continue

            try:
                snapshot = await self.client.get(
                    url, max_bytes=self.max_bytes, scope_origin=entry, scope_prefix=prefix
                )
            except DomainError as error:
                skipped.append((url, await self._failure_reason(error, host)))
                continue
            if self.limiter is not None:
                self.limiter.succeeded(host)

            if snapshot.media_type not in _SUPPORTED_MEDIA:
                skipped.append((url, "unsupported_media"))
                continue

            parsed = self._parse(url, snapshot)
            if parsed is None:
                skipped.append((url, "unsupported_media"))
                continue

            page_id = new_id()
            title = (parsed.title or url).strip()[:512]
            pages.append(
                WebCorpusPageRecord(
                    id=page_id,
                    domain=domain,
                    url=url,
                    title=title,
                    media_type=snapshot.media_type,
                    content_hash=hashlib.sha256(snapshot.body).hexdigest(),
                    byte_size=len(snapshot.body),
                )
            )
            for draft in self.chunker.chunk(parsed):
                if not draft.text.strip():
                    continue
                chunks.append(
                    WebCorpusChunkRecord(
                        page_id=page_id,
                        domain=domain,
                        url=url,
                        title=title,
                        ordinal=draft.chunk_index,
                        section_path=draft.section_path,
                        text=draft.text,
                        token_count=draft.token_count,
                    )
                )

            if depth < max_depth and snapshot.media_type == "text/html":
                for link in self._links(snapshot.body, url):
                    if link not in seen:
                        queue.append((link, depth + 1))

        # 整体替换：抓到的写入，没抓到的（页面被删、改版）随之消失。
        self.store.replace_domain(domain, pages, chunks)
        return CrawlSummary(
            domain=domain,
            pages=len(pages),
            chunks=len(chunks),
            skipped=tuple(skipped),
            truncated=truncated,
        )

    async def _wait_for_turn(self, host: str, url: str) -> bool:
        if self.limiter is None:
            return True
        crawl_delay = (
            await self.client.crawl_delay(url, self.user_agent)
            if hasattr(self.client, "crawl_delay")
            else None
        )
        return await self.limiter.acquire(host, crawl_delay=crawl_delay)

    async def _failure_reason(self, error: DomainError, host: str) -> str:
        """失败原因。探测只在失败路径上发生，结论按主机缓存。"""
        probe_result = None
        if self.probe is not None:
            probe_result = await self.probe.probe(host)
            if probe_result.state is Reachability.THROTTLED and self.limiter is not None:
                self.limiter.penalize(host, retry_after_seconds=probe_result.retry_after_seconds)
        return rejection_reason(error.code, probe_result)

    def _parse(self, url: str, snapshot) -> object | None:
        try:
            return self.parser.parse(
                DownloadedDocument(
                    title=url,
                    source_url=url,
                    media_type=snapshot.media_type,
                    raw_bytes=snapshot.body,
                )
            )
        except DomainError:
            # 解析失败按"不支持的类型"计：用户看到的处置是同一个 —— 这一页不收。
            return None

    @staticmethod
    def _links(body: bytes, url: str) -> list[str]:
        soup = BeautifulSoup(body, "html.parser")
        found: list[str] = []
        for anchor in soup.find_all("a", href=True):
            try:
                found.append(canonicalize_url(urljoin(url, anchor["href"])))
            except DomainError:
                continue
        return found
