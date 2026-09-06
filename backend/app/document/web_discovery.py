from __future__ import annotations

import hashlib
import os
import re
from collections import deque
from pathlib import Path
from urllib.parse import urljoin
from uuid import uuid4

from bs4 import BeautifulSoup

from app.api.errors import DomainError
from app.document.safe_http import SafeHttpClient, canonicalize_url, in_scope, path_prefix_for_entry
from app.schemas.batches import (
    CachedSourceRef,
    DiscoveredSource,
    DiscoveryProgress,
    DiscoveryRequest,
    DiscoveryResult,
)
from app.storage.models import CrawlEntryRecord


class WebDiscovery:
    """Bounded, same-origin crawler producing immutable local snapshots."""

    def __init__(self, client: SafeHttpClient, cache_root: Path, *, max_bytes: int = 20 * 1024 * 1024, frontier=None) -> None:
        self.client, self.cache_root, self.max_bytes, self.frontier = client, Path(cache_root), max_bytes, frontier

    async def discover(self, request: DiscoveryRequest, emit) -> DiscoveryResult:
        if request.source_kind != "web":
            raise DomainError("INVALID_REQUEST", "来源类型无效", 422, False)
        d = request.source_descriptor
        entry = canonicalize_url(str(d.get("entryUrl") or d.get("entry_url")))
        max_depth = min(int(d.get("maxDepth", 5)), 5)
        max_pages = min(int(d.get("maxPages", 200)), 200)
        prefix = path_prefix_for_entry(entry)
        queue = deque([(entry, 0)]); seen: set[str] = set(); sources=[]; rejected=[]
        if self.frontier:
            self.frontier.upsert_frontier(CrawlEntryRecord(batch_id=str(request.batch_id), canonical_url=entry, depth=0))
        if bool(d.get("useSitemap", True)):
            try:
                sm = await self.client.get(urljoin(entry, "/sitemap.xml"), max_bytes=self.max_bytes,
                                           scope_origin=entry, scope_prefix=prefix)
                for loc in re.findall(r"<loc>\s*(.*?)\s*</loc>", sm.body.decode("utf-8", "ignore")):
                    try:
                        u = canonicalize_url(loc)
                        if in_scope(u, entry, prefix):
                            queue.append((u, 0))
                            if self.frontier:
                                self.frontier.upsert_frontier(CrawlEntryRecord(batch_id=str(request.batch_id), canonical_url=u, depth=0))
                    except DomainError:
                        rejected.append({"canonical_url": loc, "reason": "scope_rejected", "message": "sitemap URL 无效"})
            except DomainError:
                pass
        while (queue or self.frontier) and len(sources) < max_pages:
            row = self.frontier.claim(str(request.batch_id)) if self.frontier else None
            if self.frontier and row is None:
                break
            if row is not None:
                url, depth = row.canonical_url, row.depth
            else:
                url, depth = queue.popleft()
            url = canonicalize_url(url)
            if url in seen or not in_scope(url, entry, prefix):
                if row: self.frontier.mark_rejected(row.id, error_code="SCOPE_REJECTED")
                continue
            seen.add(url)
            if not await self.client.robots_allowed(url, "DocMind/2", scope_origin=entry, scope_prefix=prefix):
                rejected.append({"canonical_url": url, "reason": "robots_denied", "message": "robots.txt 禁止抓取"});
                if self.frontier: self.frontier.mark_rejected(row.id if 'row' in locals() and row else "", error_code="ROBOTS_DENIED")
                continue
            try: snap = await self.client.get(url, max_bytes=self.max_bytes,
                                              scope_origin=entry, scope_prefix=prefix)
            except DomainError as e:
                rejected.append({"canonical_url": url, "reason": "fetch_failed", "message": e.message})
                if self.frontier and row: self.frontier.mark_rejected(row.id, error_code=e.code)
                continue
            media = snap.media_type
            if media not in {"text/html", "text/markdown", "application/pdf"}:
                rejected.append({"canonical_url": url, "reason": "unsupported_media", "message": "不支持的媒体类型"})
                if self.frontier and row: self.frontier.mark_rejected(row.id, error_code="UNSUPPORTED_MEDIA")
                continue
            if media == "text/html":
                soup = BeautifulSoup(snap.body, "html.parser")
                for tag in soup(["script", "style", "nav", "form", "noscript"]): tag.decompose()
                for tag in soup.select("[hidden], [aria-hidden='true']"): tag.decompose()
                title = (soup.title.string.strip() if soup.title and soup.title.string else url)
                if depth < max_depth:
                    for a in soup.find_all("a", href=True):
                        try:
                            link = canonicalize_url(urljoin(url, a["href"]))
                            if in_scope(link, entry, prefix):
                                queue.append((link, depth + 1))
                                if self.frontier: self.frontier.upsert_frontier(CrawlEntryRecord(batch_id=str(request.batch_id), canonical_url=link, depth=depth+1))
                        except DomainError: continue
            else: title = url.rsplit("/", 1)[-1] or url
            digest = hashlib.sha256(snap.body).hexdigest(); cid = uuid4()
            path = self.cache_root / "remote" / str(request.batch_id) / str(cid)
            path.parent.mkdir(parents=True, exist_ok=True)
            partial = path.with_suffix(path.suffix + ".partial")
            with partial.open("wb") as fh:
                fh.write(snap.body); fh.flush(); os.fsync(fh.fileno())
            partial.replace(path)
            sources.append(DiscoveredSource(source_identity=f"web:{url}", source_revision=digest, title=title, display_path=url, media_type=media, size_bytes=len(snap.body), cached_source=CachedSourceRef(cache_id=cid, media_type=media, byte_size=len(snap.body), sha256=digest)))
            if self.frontier and 'row' in locals() and row: self.frontier.mark_fetched(row.id, status_code=snap.status_code)
            await emit(DiscoveryProgress(stage="discovering", visited=len(seen), candidate_count=len(sources), rejected_count=len(rejected), message="网页发现中"))
        await emit(DiscoveryProgress(stage="awaiting_confirmation", visited=len(seen), candidate_count=len(sources), rejected_count=len(rejected), message="网页发现完成"))
        return DiscoveryResult(sources=sources, discovery_version=1, rejected=rejected, total_count=len(sources), rejected_count=len(rejected))
