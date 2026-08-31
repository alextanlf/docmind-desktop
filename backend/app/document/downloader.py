from __future__ import annotations

from urllib.parse import urljoin

import httpx

from app.api.errors import DomainError
from app.config import AppSettings
from app.document.sources import SourceValidator
from app.schemas.imports import DownloadedDocument


class DocumentDownloader:
    def __init__(self, settings: AppSettings) -> None:
        self.validator = SourceValidator()
        self.max_redirects = settings.max_source_redirects
        self.html_markdown_max_bytes = settings.html_markdown_max_bytes
        self.pdf_max_bytes = settings.pdf_max_bytes
        self.timeout = httpx.Timeout(
            connect=settings.source_connect_timeout_seconds,
            read=settings.source_read_timeout_seconds,
            write=settings.source_read_timeout_seconds,
            pool=settings.source_connect_timeout_seconds,
        )

    async def download_url(self, source_url: str) -> DownloadedDocument:
        current_url = self.validator.validate_url(source_url)
        redirects = 0
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
                while True:
                    async with client.stream("GET", current_url) as response:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            location = response.headers.get("location")
                            if not location:
                                raise DomainError("SOURCE_DOWNLOAD_FAILED", "重定向缺少目标地址", 502, True)
                            if redirects >= self.max_redirects:
                                raise DomainError("SOURCE_REDIRECT_LIMIT", "重定向次数超过限制", 400, False)
                            current_url = self.validator.validate_url(urljoin(current_url, location))
                            redirects += 1
                            continue
                        self._raise_for_status(response)
                        media_type, raw_bytes = await self._read_limited(response)
                        self._validate_payload(media_type, raw_bytes)
                        return DownloadedDocument(
                            title=self._title_from_url(current_url),
                            source_url=current_url,
                            media_type=media_type,
                            raw_bytes=raw_bytes,
                        )
        except DomainError:
            raise
        except httpx.TimeoutException:
            raise DomainError("SOURCE_TIMEOUT", "下载文档超时", 504, True) from None
        except httpx.RemoteProtocolError as error:
            if "location header" in str(error).lower():
                raise DomainError("SOURCE_UNSUPPORTED", "重定向地址无效", 400, False) from None
            raise DomainError("SOURCE_DOWNLOAD_FAILED", "下载文档失败", 502, True) from None
        except httpx.HTTPError:
            raise DomainError("SOURCE_DOWNLOAD_FAILED", "下载文档失败", 502, True) from None

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.is_success:
            return
        retryable = response.status_code >= 500
        raise DomainError("SOURCE_DOWNLOAD_FAILED", "文档下载失败", 502, retryable)

    @staticmethod
    def _media_type(content_type: str) -> str:
        media_type = content_type.split(";", 1)[0].strip().lower()
        if media_type == "application/pdf":
            return media_type
        if media_type in {"text/html", "application/xhtml+xml"}:
            return "text/html"
        if media_type in {"text/markdown", "text/x-markdown", "text/plain"}:
            return "text/markdown"
        raise DomainError("SOURCE_UNSUPPORTED", "不支持的文档类型", 400, False)

    async def _read_limited(self, response: httpx.Response) -> tuple[str, bytes]:
        declared_media_type = self._media_type(response.headers.get("content-type", ""))
        content_length = response.headers.get("content-length")
        declared_size: int | None = None
        if content_length:
            try:
                declared_size = int(content_length)
                if declared_size > self.pdf_max_bytes:
                    raise DomainError("DOCUMENT_TOO_LARGE", "文档超过大小限制", 413, False)
            except ValueError:
                pass
        chunks: list[bytes] = []
        size = 0
        prefix = b""
        media_type: str | None = None
        async for chunk in response.aiter_bytes():
            next_size = size + len(chunk)
            if next_size > self.pdf_max_bytes:
                raise DomainError("DOCUMENT_TOO_LARGE", "文档超过大小限制", 413, False)
            prefix = (prefix + chunk)[:5]
            if media_type is None:
                if b"%PDF-".startswith(prefix):
                    if prefix == b"%PDF-":
                        media_type = "application/pdf"
                else:
                    media_type = declared_media_type
                    if declared_media_type == "application/pdf":
                        raise DomainError("SOURCE_UNSUPPORTED", "PDF 文件签名无效", 400, False)
                if media_type is not None and media_type != declared_media_type:
                    raise DomainError("SOURCE_UNSUPPORTED", "文档类型与文件签名不匹配", 400, False)
            if media_type is not None:
                max_bytes = self.pdf_max_bytes if media_type == "application/pdf" else self.html_markdown_max_bytes
                if declared_size is not None and declared_size > max_bytes:
                    raise DomainError("DOCUMENT_TOO_LARGE", "文档超过大小限制", 413, False)
                if next_size > max_bytes:
                    raise DomainError("DOCUMENT_TOO_LARGE", "文档超过大小限制", 413, False)
            size = next_size
            chunks.append(chunk)

        media_type = media_type or declared_media_type
        if media_type == "application/pdf" and prefix != b"%PDF-":
            raise DomainError("SOURCE_UNSUPPORTED", "PDF 文件签名无效", 400, False)
        if media_type != declared_media_type:
            raise DomainError("SOURCE_UNSUPPORTED", "文档类型与文件签名不匹配", 400, False)
        max_bytes = self.pdf_max_bytes if media_type == "application/pdf" else self.html_markdown_max_bytes
        if declared_size is not None and declared_size > max_bytes:
            raise DomainError("DOCUMENT_TOO_LARGE", "文档超过大小限制", 413, False)
        if size > max_bytes:
            raise DomainError("DOCUMENT_TOO_LARGE", "文档超过大小限制", 413, False)
        return media_type, b"".join(chunks)

    @staticmethod
    def _validate_payload(media_type: str, raw_bytes: bytes) -> None:
        if media_type == "application/pdf" and not raw_bytes.startswith(b"%PDF-"):
            raise DomainError("SOURCE_UNSUPPORTED", "PDF 文件签名无效", 400, False)
        if media_type == "text/markdown":
            try:
                raw_bytes.decode("utf-8-sig")
            except UnicodeDecodeError:
                raise DomainError("SOURCE_UNSUPPORTED", "Markdown 必须使用 UTF-8 编码", 400, False) from None

    @staticmethod
    def _title_from_url(source_url: str) -> str:
        path = httpx.URL(source_url).path.rstrip("/")
        return path.rsplit("/", 1)[-1] or "document"
