from __future__ import annotations

import asyncio
import hashlib
import os
import re
from pathlib import Path
from uuid import uuid4

from app.api.errors import DomainError
from app.schemas.batches import (
    CachedSourceRef,
    DiscoveredSource,
    DiscoveryProgress,
    DiscoveryRequest,
    DiscoveryResult,
    RemoteBinding,
)
from app.yuque.gateway import YuqueGateway

_MARKER_RE = re.compile(r"\n?<!--\s*docmind[^>]*-->\s*", re.IGNORECASE)


class YuqueDiscovery:
    """Read-only Yuque repository snapshot discovery."""

    def __init__(self, gateway: YuqueGateway, repository_store, cache_root: Path) -> None:
        self.gateway = gateway
        self.repository_store = repository_store
        self.cache_root = Path(cache_root)
        self._lock = getattr(gateway, "_context_lock", asyncio.Lock())

    async def discover(self, request: DiscoveryRequest, emit) -> DiscoveryResult:
        if request.source_kind != "yuque_repository":
            raise DomainError("INVALID_REQUEST", "来源类型无效", 422, False)
        descriptor = request.source_descriptor
        remote_repo = str(descriptor.get("repositoryId") or descriptor.get("repository_id") or "")
        if not remote_repo:
            raise DomainError("YUQUE_DISCOVERY_FAILED", "缺少语雀知识库标识", 422, False)
        local = self.repository_store.get(str(request.repository_id)) if request.repository_id else None
        if local is not None and remote_repo == str(request.repository_id):
            if not local.yuque_id:
                raise DomainError("YUQUE_DISCOVERY_FAILED", "知识库尚未绑定语雀", 409, False)
            remote_repo = local.yuque_id
        if local is not None and local.yuque_id and local.yuque_id != remote_repo:
            raise DomainError("YUQUE_DISCOVERY_FAILED", "语雀知识库不匹配", 409, False)
        try:
            async with self._lock:
                docs = await self.gateway.list_documents(remote_repo)
                docs = docs[:1000]
                sources: list[DiscoveredSource] = []
                for doc in docs:
                    content_obj = await self.gateway.read_document(doc.yuque_id)
                    content = _MARKER_RE.sub("\n", content_obj.content).strip() + "\n"
                    raw = content.encode("utf-8")
                    digest = hashlib.sha256(raw).hexdigest()
                    cid = uuid4()
                    path = self.cache_root / "remote" / str(request.batch_id) / str(cid)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    partial = path.with_suffix(path.suffix + ".partial")
                    with partial.open("wb") as fh:
                        fh.write(raw)
                        fh.flush()
                        os.fsync(fh.fileno())
                    partial.replace(path)
                    sources.append(DiscoveredSource(
                        source_identity=f"yuque:{remote_repo}:{doc.yuque_id}",
                        source_revision=digest,
                        title=doc.title,
                        display_path=doc.url or doc.yuque_id,
                        media_type="text/markdown",
                        size_bytes=len(raw),
                        cached_source=CachedSourceRef(cache_id=cid, media_type="text/markdown", byte_size=len(raw), sha256=digest),
                        remote_binding=RemoteBinding(repository_id=remote_repo, document_id=doc.yuque_id, document_url=doc.url),
                    ))
                    await emit(DiscoveryProgress(stage="discovering", visited=len(sources), candidate_count=len(sources), rejected_count=0, message="语雀文档发现中"))
        except DomainError:
            raise
        except Exception as exc:
            raise DomainError("YUQUE_DISCOVERY_FAILED", "语雀文档发现失败", 503, True) from exc
        await emit(DiscoveryProgress(stage="awaiting_confirmation", visited=len(sources), candidate_count=len(sources), rejected_count=0, message="语雀发现完成"))
        return DiscoveryResult(sources=sources, discovery_version=1, total_count=len(sources), rejected_count=0)
