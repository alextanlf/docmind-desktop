from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.api.errors import DomainError
from app.document.staging_manifest import (
    ManifestFile,
    load_manifest,
    sha256_file,
    validate_manifest,
)
from app.schemas.batches import (
    CachedSourceRef,
    DiscoveredSource,
    DiscoveryProgress,
    DiscoveryRequest,
    DiscoveryResult,
)

Emit = Callable[[DiscoveryProgress], Awaitable[None] | None]


def read_cached_source(cache_ref: str | Path, staging_root: Path | None = None) -> Path:
    """Resolve a relative cache reference under the configured staging root."""
    value = Path(cache_ref)
    if value.is_absolute() or ".." in value.parts:
        raise DomainError("BATCH_SOURCE_CHANGED", "暂存文件路径无效", 409, False)
    if staging_root is None:
        return value
    root = Path(staging_root).resolve()
    resolved = (root / value).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise DomainError("BATCH_SOURCE_CHANGED", "暂存文件路径无效", 409, False) from None
    return resolved


class DirectoryDiscovery:
    def __init__(self, staging_root: Path) -> None:
        self.root = Path(staging_root).resolve()

    def _manifest_path(self, collection_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", collection_id):
            raise DomainError("BATCH_SOURCE_CHANGED", "暂存集合标识无效", 409, False)
        path = self.root / "collections" / collection_id / "manifest.json"
        try:
            path.resolve().relative_to(self.root)
        except ValueError:
            raise DomainError("BATCH_SOURCE_CHANGED", "暂存集合路径无效", 409, False) from None
        return path

    async def discover(self, request: DiscoveryRequest, emit: Emit) -> DiscoveryResult:
        if request.source_kind != "staged_directory":
            raise DomainError("INVALID_REQUEST", "来源类型无效", 422, False)
        collection_id = request.source_descriptor.get("collectionId")
        if not isinstance(collection_id, str):
            raise DomainError("BATCH_SOURCE_CHANGED", "暂存集合标识无效", 409, False)
        collection_root = self._manifest_path(collection_id).parent
        manifest = load_manifest(self._manifest_path(collection_id))
        entries = validate_manifest(manifest, collection_root)
        entries.sort(key=lambda entry: (entry.ordinal if entry.ordinal is not None else 1_000_000, entry.relative_path))
        sources = [self._candidate(entry, manifest.root_id, collection_root) for entry in entries]
        event = DiscoveryProgress(
            stage="awaiting_confirmation",
            visited=len(sources),
            candidate_count=len(sources),
            rejected_count=0,
            message="目录发现完成",
        )
        emitted = emit(event)
        if inspect.isawaitable(emitted):
            await emitted
        return DiscoveryResult(
            sources=sources,
            discovery_version=1,
            rejected=[],
            total_count=len(sources),
            rejected_count=0,
        )

    def _candidate(
        self,
        entry: ManifestFile,
        root_id: str,
        collection_root: Path,
    ) -> DiscoveredSource:
        path = collection_root / "items" / str(entry.staged_id)
        # Re-check immediately before returning the candidate to detect replacements.
        revision = sha256_file(path)
        if revision != entry.sha256:
            raise DomainError("BATCH_SOURCE_CHANGED", "暂存文件已变化", 409, False)
        title = Path(entry.relative_path).name
        if entry.media_type == "text/markdown":
            try:
                text = path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeDecodeError):
                raise DomainError("BATCH_SOURCE_CHANGED", "暂存文件编码无效", 409, False) from None
            for line in text.splitlines():
                match = re.match(r"^#\s+(.+?)\s*$", line)
                if match and match.group(1).strip():
                    title = match.group(1).strip().rstrip("#").strip()
                    break
        elif entry.media_type == "text/html":
            try:
                path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                raise DomainError("BATCH_SOURCE_CHANGED", "暂存文件编码无效", 409, False) from None
        return DiscoveredSource(
            source_identity=f"folder:{root_id}:{entry.relative_path}",
            source_revision=revision,
            title=title,
            display_path=entry.relative_path,
            media_type=entry.media_type,
            size_bytes=entry.size_bytes,
            cached_source=CachedSourceRef(
                cache_id=entry.staged_id,
                media_type=entry.media_type,
                byte_size=entry.size_bytes,
                sha256=revision,
            ),
            remote_binding=None,
        )

    def read_cached_source(self, cache_ref: CachedSourceRef, collection_id: str) -> Path:
        return read_cached_source(
            Path("collections") / collection_id / "items" / str(cache_ref.cache_id), self.root
        )
