from __future__ import annotations

import ipaddress
import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from app.api.errors import DomainError
from app.config import AppSettings
from app.schemas.imports import DownloadedDocument, SourcePreview, SourceRef

if TYPE_CHECKING:
    from app.document.downloader import DocumentDownloader

_SUPPORTED_STAGED_SUFFIXES = {".md", ".markdown", ".pdf"}


def _unsupported(message: str = "不支持的文档来源") -> DomainError:
    return DomainError("SOURCE_UNSUPPORTED", message, 400, False)


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
            source_url=path.as_uri(),
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


class SourceInspector:
    def __init__(self, settings: AppSettings, downloader: DocumentDownloader | None = None) -> None:
        self.staged_store = StagedFileStore(settings)
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
