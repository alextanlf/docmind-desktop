from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import UUID

from app.api.errors import DomainError
from app.document.builtin_formats import builtin_registry
from app.document.formats import FormatRegistry, size_limit_for
from app.document.markdown_sections import title_from_markdown
from app.document.staging_manifest import (
    ManifestFile,
    load_manifest,
    read_file_snapshot,
    validate_manifest,
    verify_file_snapshot,
)
from app.schemas.batches import (
    CachedSourceRef,
    DiscoveredSource,
    DiscoveryProgress,
    DiscoveryRequest,
    DiscoveryResult,
)

Emit = Callable[[DiscoveryProgress], Awaitable[None] | None]


def _uuid_segment(value: str, message: str) -> str:
    if not isinstance(value, str):
        raise DomainError("BATCH_SOURCE_CHANGED", message, 409, False)
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError):
        raise DomainError("BATCH_SOURCE_CHANGED", message, 409, False) from None
    if str(parsed) != value.lower():
        raise DomainError("BATCH_SOURCE_CHANGED", message, 409, False)
    return str(parsed)


def read_cached_source(
    cache_ref: CachedSourceRef,
    staging_root: Path | None = None,
    collection_id: str | None = None,
    formats: FormatRegistry | None = None,
) -> Path:
    """Verify and resolve a staged source under a configured root.

    The returned path is a verified logical path; callers must consume it
    immediately because a later path replacement cannot be prevented after
    this function returns.
    """
    registry = formats if formats is not None else builtin_registry()
    if staging_root is None:
        raise DomainError("BATCH_SOURCE_CHANGED", "暂存根目录未配置", 409, False)
    if not isinstance(cache_ref, CachedSourceRef) or collection_id is None:
        raise DomainError("BATCH_SOURCE_CHANGED", "暂存文件引用无效", 409, False)
    collection_uuid = _uuid_segment(collection_id, "暂存集合标识无效")
    cache_uuid = _uuid_segment(str(cache_ref.cache_id), "暂存文件标识无效")
    format = registry.for_media_type(cache_ref.media_type)
    if format is None:
        raise DomainError("BATCH_SOURCE_CHANGED", "暂存文件类型无效", 409, False)
    root = Path(staging_root).resolve()
    path = root / "collections" / collection_uuid / "items" / cache_uuid
    try:
        path.resolve().relative_to(root)
    except ValueError:
        raise DomainError("BATCH_SOURCE_CHANGED", "暂存文件路径无效", 409, False) from None
    max_bytes = size_limit_for(
        format, binary_max_bytes=100 * 1024 * 1024, text_max_bytes=20 * 1024 * 1024
    )
    read_file_snapshot(
        path,
        expected_size=cache_ref.byte_size,
        expected_hash=cache_ref.sha256,
        max_bytes=max_bytes,
    )
    return path


class DirectoryDiscovery:
    def __init__(
        self,
        staging_root: Path,
        manifest_max_bytes: int = 2 * 1024 * 1024,
        formats: FormatRegistry | None = None,
    ) -> None:
        self.root = Path(staging_root).resolve()
        self.manifest_max_bytes = manifest_max_bytes
        self.formats = formats if formats is not None else builtin_registry()

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
        manifest = load_manifest(self._manifest_path(collection_id), max_bytes=self.manifest_max_bytes)
        entries = validate_manifest(manifest, collection_root, self.formats)
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
        format = self.formats.for_media_type(entry.media_type)
        if format is None:
            raise DomainError("BATCH_SOURCE_CHANGED", "暂存文件类型无效", 409, False)
        # Derive every candidate field from one securely opened descriptor.
        max_bytes = size_limit_for(
            format, binary_max_bytes=100 * 1024 * 1024, text_max_bytes=20 * 1024 * 1024
        )
        snapshot = read_file_snapshot(
            path,
            expected_size=entry.size_bytes,
            expected_hash=entry.sha256,
            max_bytes=max_bytes,
            collect_bytes=format.validate is not None,
        )
        verify_file_snapshot(path, snapshot)
        revision = snapshot.digest
        title = Path(entry.relative_path).name
        if format.validate is not None:
            problem = format.validate(snapshot.raw_bytes or b"")
            if problem is not None:
                raise DomainError("BATCH_SOURCE_CHANGED", problem, 409, False)
        if entry.media_type == "text/markdown":
            text = (snapshot.raw_bytes or b"").decode("utf-8-sig")
            title = title_from_markdown(text) or title
        return DiscoveredSource(
            source_identity=f"folder:{root_id}:{entry.relative_path}",
            source_revision=revision,
            title=title,
            display_path=entry.relative_path,
            media_type=entry.media_type,
            size_bytes=entry.size_bytes,
            cached_source=CachedSourceRef(
                cache_id=UUID(entry.staged_id),
                media_type=entry.media_type,
                byte_size=entry.size_bytes,
                sha256=revision,
            ),
            remote_binding=None,
        )

    def read_cached_source(self, cache_ref: CachedSourceRef, collection_id: str) -> Path:
        return read_cached_source(cache_ref, self.root, collection_id)
