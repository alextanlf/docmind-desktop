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
from app.storage.repositories import CorpusPageWrite, WebCorpusStore

#: 只收能够解析成正文的类型。与导入路径同一张白名单。
_SUPPORTED_MEDIA = {"text/html", "text/markdown", "application/pdf"}

#: 一次抓取的默认预算。三项都小得"能被用户等到"，因为这是按钮触发的动作。
DEFAULT_MAX_PAGES = 20
DEFAULT_MAX_DEPTH = 2


@dataclass(frozen=True)
class CrawlSummary:
    domain: str
    #: 抓取**之后**该域名的语料规模（不是这次下载了几页）。
    pages: int
    chunks: int
    #: 内容未变、没有重新切块与重写索引的页数。增量刷新的收益就体现在这里。
    kept_pages: int
    #: 新抓到的、或内容变了因而重写的页数。
    updated_pages: int
    #: 有证据表明已消失（404 / 410）因而删掉本地副本的页数。
    removed_pages: int
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


def _conditional_headers(previous: WebCorpusPageRecord | None) -> dict[str, str] | None:
    """带上上次拿到的校验器，让站点有机会直接答 304。

    两个都带上：`ETag` 比 `Last-Modified` 更强（能区分"同一秒内改了两次"），但并非
    所有站点都给 ETag，而 `Last-Modified` 更常见。
    """
    if previous is None:
        return None
    headers: dict[str, str] = {}
    if previous.etag:
        headers["If-None-Match"] = previous.etag
    if previous.last_modified:
        headers["If-Modified-Since"] = previous.last_modified
    return headers or None


def _refresh(previous: WebCorpusPageRecord, snapshot) -> WebCorpusPageRecord:
    """内容未变时只更新校验器与抓取时间，标题/正文/块都保持原样。"""
    return WebCorpusPageRecord(
        id=previous.id,
        domain=previous.domain,
        url=previous.url,
        title=previous.title,
        media_type=previous.media_type,
        content_hash=previous.content_hash,
        byte_size=previous.byte_size,
        etag=snapshot.headers.get("etag") or previous.etag,
        last_modified=snapshot.headers.get("last-modified") or previous.last_modified,
    )


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
        visited: set[str] = set()
        skipped: list[tuple[str, str]] = []
        writes: list[CorpusPageWrite] = []
        gone: list[str] = []
        existing = {row.url: row for row in self.store.pages(domain)}
        fetched = 0
        kept = 0
        updated = 0
        truncated = False

        while queue:
            if fetched >= max_pages:
                truncated = True
                break
            url, depth = queue.popleft()
            url = canonicalize_url(url)
            if url in visited or not in_scope(url, entry, prefix):
                continue
            visited.add(url)

            if not await self.client.robots_allowed(
                url, self.user_agent, scope_origin=entry, scope_prefix=prefix
            ):
                skipped.append((url, "robots_denied"))
                continue

            host = urlsplit(url).hostname or domain
            if not await self._wait_for_turn(host, url):
                skipped.append((url, "limit_reached"))
                continue

            previous = existing.get(url)
            try:
                snapshot = await self.client.get(
                    url,
                    max_bytes=self.max_bytes,
                    scope_origin=entry,
                    scope_prefix=prefix,
                    headers=_conditional_headers(previous),
                )
            except DomainError as error:
                reason = await self._failure_reason(error, host)
                skipped.append((url, reason))
                # 只有站点**明确说**这一页没了（404 / 410）才删本地副本。超时、限流、
                # 5xx 都不算证据 —— 把不确定当成不存在，会让语料在一次网络抖动后缩水。
                if getattr(error, "upstream_status", None) in (404, 410) and previous is not None:
                    gone.append(url)
                continue
            if self.limiter is not None:
                self.limiter.succeeded(host)
            fetched += 1

            # 304：站点说"没变"。正文都没下载，所以这一页上的链接这次不会被重新发现
            # （新增子页面要等下一次抓到它时才会被发现）；换来的是完全省下下载与切块。
            if snapshot.status_code == 304 and previous is not None:
                writes.append(
                    CorpusPageWrite(page=_refresh(previous, snapshot), chunks=None)
                )
                kept += 1
                continue

            if snapshot.media_type not in _SUPPORTED_MEDIA:
                skipped.append((url, "unsupported_media"))
                continue

            digest = hashlib.sha256(snapshot.body).hexdigest()
            if previous is not None and previous.content_hash == digest:
                # 200 但内容一致（站点没给校验器）。同样不重新切块。
                writes.append(CorpusPageWrite(page=_refresh(previous, snapshot), chunks=None))
                kept += 1
            else:
                parsed = self._parse(url, snapshot)
                if parsed is None:
                    skipped.append((url, "unsupported_media"))
                    continue
                page_id = new_id()
                title = (parsed.title or url).strip()[:512]
                writes.append(
                    CorpusPageWrite(
                        page=WebCorpusPageRecord(
                            id=page_id,
                            domain=domain,
                            url=url,
                            title=title,
                            media_type=snapshot.media_type,
                            content_hash=digest,
                            byte_size=len(snapshot.body),
                            etag=snapshot.headers.get("etag"),
                            last_modified=snapshot.headers.get("last-modified"),
                        ),
                        chunks=tuple(
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
                            for draft in self.chunker.chunk(parsed)
                            if draft.text.strip()
                        ),
                    )
                )
                updated += 1

            # 有正文就重新发现链接（内容没变的那一支也有正文，不该跳过）。
            if depth < max_depth and snapshot.media_type == "text/html":
                for link in self._links(snapshot.body, url):
                    if link not in visited:
                        queue.append((link, depth + 1))

        self.store.apply_crawl(domain, writes, removed_urls=gone)
        stats = self.store.stats().get(domain)
        return CrawlSummary(
            domain=domain,
            pages=stats.pages if stats else 0,
            chunks=stats.chunks if stats else 0,
            kept_pages=kept,
            updated_pages=updated,
            removed_pages=len(gone),
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
        """失败原因。

        **有上游状态码就不探测**：站点已经亲口说了是什么问题（404/429/451…），再发一次
        请求去猜一遍，既慢又是多余的网络往来。只有传输层失败（DNS、超时、TLS）时确实
        没有状态码可看，才需要探测。
        """
        upstream_status = getattr(error, "upstream_status", None)
        if upstream_status is not None:
            return rejection_reason(error.code, upstream_status=upstream_status)
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
