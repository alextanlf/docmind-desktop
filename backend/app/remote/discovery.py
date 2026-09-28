from __future__ import annotations

import asyncio
import hashlib
import os
import re
from pathlib import Path
from uuid import uuid4

from app.api.errors import DomainError
from app.remote.registry import ProviderRegistry
from app.schemas.batches import (
    CachedSourceRef,
    DiscoveredSource,
    DiscoveryProgress,
    DiscoveryRequest,
    DiscoveryResult,
    RemoteBinding,
)

# Any embedded DocMind markup comment is stripped from imported remote content.
_DOCMIND_MARKUP_RE = re.compile(r"\n?<!--\s*docmind[^>]*-->\s*", re.IGNORECASE)


class RemoteDiscovery:
    """Read-only remote repository snapshot discovery for any provider."""

    def __init__(
        self,
        registry: ProviderRegistry,
        repository_store,
        cache_root: Path,
    ) -> None:
        self.registry = registry
        self.repository_store = repository_store
        self.cache_root = Path(cache_root)
        self._lock = asyncio.Lock()

    async def discover(self, request: DiscoveryRequest, emit) -> DiscoveryResult:
        if request.source_kind != "remote_repository":
            raise DomainError("INVALID_REQUEST", "来源类型无效", 422, False)
        descriptor = request.source_descriptor
        local = self.repository_store.get(str(request.repository_id)) if request.repository_id else None
        provider_name = str(descriptor.get("provider") or (local.provider if local else "") or "")
        if not provider_name:
            raise DomainError("REMOTE_DISCOVERY_FAILED", "缺少远程知识库来源", 422, False)
        provider = self.registry.get(provider_name)
        remote_repo = str(descriptor.get("repositoryId") or descriptor.get("repository_id") or "")
        if not remote_repo:
            raise DomainError("REMOTE_DISCOVERY_FAILED", "缺少远程知识库标识", 422, False)
        if local is not None and remote_repo == str(request.repository_id):
            if not local.remote_id:
                raise DomainError("REMOTE_DISCOVERY_FAILED", "知识库尚未绑定远程来源", 409, False)
            remote_repo = local.remote_id
        if local is not None and local.remote_id and local.remote_id != remote_repo:
            raise DomainError("REMOTE_DISCOVERY_FAILED", "远程知识库不匹配", 409, False)
        try:
            async with self._lock:
                docs = await provider.list_documents(remote_repo)
                docs = docs[:1000]
                sources: list[DiscoveredSource] = []
                for doc in docs:
                    content_obj = await provider.read_document(doc.remote_id)
                    content = _DOCMIND_MARKUP_RE.sub("\n", content_obj.content).strip() + "\n"
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
                        source_identity=f"{provider_name}:{remote_repo}:{doc.remote_id}",
                        source_revision=digest,
                        title=doc.title,
                        display_path=doc.url or doc.remote_id,
                        media_type="text/markdown",
                        size_bytes=len(raw),
                        cached_source=CachedSourceRef(cache_id=cid, media_type="text/markdown", byte_size=len(raw), sha256=digest),
                        remote_binding=RemoteBinding(
                            provider=provider_name,
                            repository_id=remote_repo,
                            document_id=doc.remote_id,
                            document_url=doc.url,
                        ),
                    ))
                    await emit(DiscoveryProgress(stage="discovering", visited=len(sources), candidate_count=len(sources), rejected_count=0, message="远程文档发现中"))
        except DomainError:
            raise
        except Exception as exc:
            raise DomainError("REMOTE_DISCOVERY_FAILED", "远程文档发现失败", 503, True) from exc
        await emit(DiscoveryProgress(stage="awaiting_confirmation", visited=len(sources), candidate_count=len(sources), rejected_count=0, message="远程发现完成"))
        return DiscoveryResult(sources=sources, discovery_version=1, total_count=len(sources), rejected_count=0)
