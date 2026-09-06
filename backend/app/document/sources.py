from __future__ import annotations

import ipaddress
import os
import stat
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from app.api.errors import DomainError
from app.config import AppSettings
from app.document.staging_manifest import (
    ManifestFile,
    StagingManifest,
    load_manifest,
    read_file_snapshot,
    validate_manifest,
    verify_file_snapshot,
)
from app.schemas.batches import CachedSourceRef
from app.schemas.imports import DownloadedDocument, SourcePreview, SourceRef

if TYPE_CHECKING:
    from app.document.downloader import DocumentDownloader

_SUPPORTED_STAGED_SUFFIXES = {".md", ".markdown", ".pdf"}


def _unsupported(message: str = "不支持的文档来源") -> DomainError:
    return DomainError("SOURCE_UNSUPPORTED", message, 400, False)


def _batch_source_changed(message: str = "暂存目录内容已变化") -> DomainError:
    return DomainError("BATCH_SOURCE_CHANGED", message, 409, False)


@dataclass(frozen=True)
class CollectionCacheRequest:
    key: str
    cache_ref: CachedSourceRef
    source_identity: str
    source_revision: str
    display_path: str
    title: str


class SourceValidator:
    def validate_url(self, value: str) -> str:
        from urllib.parse import urlsplit

        try:
            parsed = urlsplit(value)
            host = parsed.hostname
            if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
                raise ValueError
            literal_ip = _literal_ip(host)
            if literal_ip is not None and not literal_ip.is_global:
                raise ValueError
        except (TypeError, ValueError):
            raise _unsupported() from None
        return value


def _literal_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass

    parts = host.split(".")
    numeric_parts: list[int] = []
    for part in parts:
        if not (part.isdecimal() or part.lower().startswith("0x")):
            return None
        numeric_parts.append(_inet_number(part))
    if not numeric_parts or len(numeric_parts) > 4:
        raise ValueError

    widths = {1: [32], 2: [8, 24], 3: [8, 8, 16], 4: [8, 8, 8, 8]}[len(numeric_parts)]
    if any(value >= 1 << width for value, width in zip(numeric_parts, widths, strict=True)):
        raise ValueError
    value = 0
    for part, width in zip(numeric_parts, widths, strict=True):
        value = (value << width) | part
    return ipaddress.IPv4Address(value)


def _inet_number(value: str) -> int:
    if not value:
        raise ValueError
    if value.lower().startswith("0x"):
        return int(value, 0)
    if len(value) > 1 and value.startswith("0"):
        return int(value[1:], 8)
    if not value.isdecimal():
        raise ValueError
    return int(value, 10)


class StagedFileStore:
    def __init__(self, settings: AppSettings) -> None:
        self.staging_dir = settings.staging_dir
        self.html_markdown_max_bytes = settings.html_markdown_max_bytes
        self.pdf_max_bytes = settings.pdf_max_bytes

    def resolve(self, staged_id: str) -> Path:
        try:
            canonical_id = str(UUID(staged_id))
        except (TypeError, ValueError, AttributeError):
            raise _unsupported() from None
        if canonical_id != staged_id.lower():
            raise _unsupported()

        root = self.staging_dir.resolve()
        matches = [path for path in self.staging_dir.glob(f"{canonical_id}.*") if path.parent == self.staging_dir]
        if len(matches) != 1:
            raise _unsupported("暂存文件不存在或不唯一")
        if matches[0].suffix.lower() not in _SUPPORTED_STAGED_SUFFIXES:
            raise _unsupported()
        resolved = matches[0].resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            raise _unsupported("暂存文件不在允许目录内") from None
        if not resolved.is_file():
            raise _unsupported("暂存文件不可用")
        return resolved

    def load(self, staged_id: str) -> DownloadedDocument:
        path = self.resolve(staged_id)
        media_type = self._media_type(path)
        size_limit = self.pdf_max_bytes if media_type == "application/pdf" else self.html_markdown_max_bytes
        raw_bytes = self._read_limited(path, size_limit)
        if media_type == "application/pdf" and not raw_bytes.startswith(b"%PDF-"):
            raise _unsupported("PDF 文件签名无效")
        if media_type == "text/markdown":
            try:
                raw_bytes.decode("utf-8-sig")
            except UnicodeDecodeError:
                raise _unsupported("Markdown 必须使用 UTF-8 编码") from None
        return DownloadedDocument(
            title=path.name,
            # Preserve only the opaque staged identifier across the backend
            # boundary; never expose the absolute staging path in previews,
            # persisted chunks, or later chat citations.
            source_url=f"staged://{staged_id.lower()}",
            media_type=media_type,
            raw_bytes=raw_bytes,
            local_path=path,
        )

    @staticmethod
    def _read_limited(path: Path, size_limit: int) -> bytes:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError:
            raise _unsupported("暂存文件不可用") from None
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise _unsupported("暂存文件不可用")
            if metadata.st_size > size_limit:
                raise DomainError("DOCUMENT_TOO_LARGE", "文档超过大小限制", 413, False)
            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = os.read(descriptor, min(64 * 1024, size_limit + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > size_limit:
                    raise DomainError("DOCUMENT_TOO_LARGE", "文档超过大小限制", 413, False)
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            os.close(descriptor)

    @staticmethod
    def _media_type(path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return "application/pdf"
        if suffix in {".md", ".markdown"}:
            return "text/markdown"
        raise _unsupported()


class CollectionCacheStore:
    """Internal reader for immutable Phase 2 collection snapshots."""

    def __init__(self, settings: AppSettings) -> None:
        self.staging_dir = settings.staging_dir
        self.manifest_max_bytes = settings.staging_manifest_max_bytes
        self.html_markdown_max_bytes = settings.html_markdown_max_bytes
        self.pdf_max_bytes = settings.pdf_max_bytes

    def load_many(
        self,
        collection_id: str,
        requests: Iterable[CollectionCacheRequest],
    ) -> dict[str, DownloadedDocument]:
        canonical_collection_id = self._uuid(collection_id, "暂存集合标识无效")
        root = self.staging_dir.resolve()
        collection_root = root / "collections" / canonical_collection_id
        try:
            collection_root.resolve().relative_to(root)
        except ValueError:
            raise _batch_source_changed("暂存集合路径无效") from None
        manifest = load_manifest(
            collection_root / "manifest.json", max_bytes=self.manifest_max_bytes
        )
        entries = validate_manifest(manifest, collection_root)
        entries_by_id = {entry.staged_id: entry for entry in entries}
        loaded: dict[str, DownloadedDocument] = {}
        for request in requests:
            cache_id = str(request.cache_ref.cache_id)
            entry = entries_by_id.get(cache_id)
            if entry is None:
                raise _batch_source_changed("暂存文件不在清单中")
            self._validate_descriptor(manifest, entry, request)
            loaded[request.key] = self._load_entry(
                collection_root,
                canonical_collection_id,
                entry,
                request,
            )
        return loaded

    def load(
        self,
        collection_id: str,
        request: CollectionCacheRequest,
    ) -> DownloadedDocument:
        return self.load_many(collection_id, [request])[request.key]

    @staticmethod
    def _uuid(value: str, message: str) -> str:
        try:
            parsed = UUID(value)
        except (TypeError, ValueError, AttributeError):
            raise _batch_source_changed(message) from None
        if str(parsed) != value.lower():
            raise _batch_source_changed(message)
        return str(parsed)

    @staticmethod
    def _validate_descriptor(
        manifest: StagingManifest,
        entry: ManifestFile,
        request: CollectionCacheRequest,
    ) -> None:
        cache_ref = request.cache_ref
        expected_identity = f"folder:{manifest.root_id}:{entry.relative_path}"
        if (
            request.source_identity != expected_identity
            or request.source_revision != entry.sha256
            or request.display_path != entry.relative_path
            or str(cache_ref.cache_id) != entry.staged_id
            or cache_ref.media_type != entry.media_type
            or cache_ref.byte_size != entry.size_bytes
            or cache_ref.sha256 != entry.sha256
        ):
            raise _batch_source_changed("暂存文件描述已变化")

    def _load_entry(
        self,
        collection_root: Path,
        collection_id: str,
        entry: ManifestFile,
        request: CollectionCacheRequest,
    ) -> DownloadedDocument:
        path = collection_root / "items" / entry.staged_id
        size_limit = (
            self.pdf_max_bytes
            if entry.media_type == "application/pdf"
            else self.html_markdown_max_bytes
        )
        snapshot = read_file_snapshot(
            path,
            expected_size=entry.size_bytes,
            expected_hash=entry.sha256,
            max_bytes=size_limit,
            collect_bytes=True,
        )
        verify_file_snapshot(path, snapshot)
        raw_bytes = snapshot.raw_bytes or b""
        if entry.media_type == "application/pdf" and not raw_bytes.startswith(b"%PDF-"):
            raise _batch_source_changed("PDF 文件签名无效")
        if entry.media_type in {"text/markdown", "text/html"}:
            try:
                raw_bytes.decode("utf-8-sig" if entry.media_type == "text/markdown" else "utf-8")
            except UnicodeDecodeError:
                raise _batch_source_changed("暂存文件编码无效") from None
        return DownloadedDocument(
            title=request.title,
            source_url=f"staged-collection://{collection_id}/{entry.staged_id}",
            media_type=entry.media_type,
            raw_bytes=raw_bytes,
            local_path=path,
        )


class SourceInspector:
    def __init__(self, settings: AppSettings, downloader: DocumentDownloader | None = None) -> None:
        self.staged_store = StagedFileStore(settings)
        self.collection_store = CollectionCacheStore(settings)
        if downloader is None:
            from app.document.downloader import DocumentDownloader

            downloader = DocumentDownloader(settings)
        self.downloader = downloader

    async def inspect(self, ref: SourceRef) -> SourcePreview:
        document = await self.load(ref)
        return SourcePreview.from_document(ref.kind, document)

    async def load(self, ref: SourceRef) -> DownloadedDocument:
        if ref.kind == "url":
            return await self.downloader.download_url(ref.value)
        return self.staged_store.load(ref.value)

    def load_collection_cache(
        self,
        *,
        collection_id: str,
        cache_ref: CachedSourceRef,
        source_identity: str,
        source_revision: str,
        display_path: str,
        title: str,
    ) -> DownloadedDocument:
        if collection_id.startswith("remote/"):
            try:
                batch_id = UUID(collection_id.removeprefix("remote/"))
                cache_id = UUID(str(cache_ref.cache_id))
            except ValueError as error:
                raise DomainError("SOURCE_NOT_FOUND", "远程缓存标识无效", 404, False) from error
            staging_root = self.collection_store.staging_dir.resolve()
            path = (staging_root / "remote" / str(batch_id) / str(cache_id)).resolve()
            if staging_root not in path.parents:
                raise DomainError("SOURCE_NOT_FOUND", "远程缓存路径无效", 404, False)
            size_limit = (
                self.collection_store.pdf_max_bytes
                if cache_ref.media_type == "application/pdf"
                else self.collection_store.html_markdown_max_bytes
            )
            snapshot = read_file_snapshot(
                path,
                expected_size=cache_ref.byte_size,
                expected_hash=cache_ref.sha256,
                max_bytes=size_limit,
                collect_bytes=True,
            )
            verify_file_snapshot(path, snapshot)
            return DownloadedDocument(title=title, source_url=display_path, media_type=cache_ref.media_type, raw_bytes=snapshot.raw_bytes or b"", local_path=path)
        return self.collection_store.load(
            collection_id,
            CollectionCacheRequest(
                key=str(cache_ref.cache_id),
                cache_ref=cache_ref,
                source_identity=source_identity,
                source_revision=source_revision,
                display_path=display_path,
                title=title,
            ),
        )

    def load_collection_caches(
        self,
        collection_id: str,
        requests: Iterable[CollectionCacheRequest],
    ) -> dict[str, DownloadedDocument]:
        if collection_id.startswith("remote/"):
            return {
                request.key: self.load_collection_cache(
                    collection_id=collection_id,
                    cache_ref=request.cache_ref,
                    source_identity=request.source_identity,
                    source_revision=request.source_revision,
                    display_path=request.display_path,
                    title=request.title,
                )
                for request in requests
            }
        return self.collection_store.load_many(collection_id, requests)
