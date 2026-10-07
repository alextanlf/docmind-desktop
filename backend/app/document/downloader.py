from __future__ import annotations

from urllib.parse import urljoin

import httpx

from app.api.errors import DomainError
from app.config import AppSettings
from app.document.builtin_formats import builtin_registry
from app.document.formats import FormatRegistry, size_limit_for
from app.document.sources import SourceValidator
from app.schemas.imports import DownloadedDocument


class DocumentDownloader:
    def __init__(self, settings: AppSettings, formats: FormatRegistry | None = None) -> None:
        self.validator = SourceValidator()
        self.max_redirects = settings.max_source_redirects
        self.html_markdown_max_bytes = settings.html_markdown_max_bytes
        self.pdf_max_bytes = settings.pdf_max_bytes
        self.formats = formats if formats is not None else builtin_registry()
        self.timeout = httpx.Timeout(
            connect=settings.source_connect_timeout_seconds,
            read=settings.source_read_timeout_seconds,
            write=settings.source_read_timeout_seconds,
            pool=settings.source_connect_timeout_seconds,
        )

    def _limit_for(self, media_type: str) -> int:
        format = self.formats.for_media_type(media_type)
        if format is None:
            return self.html_markdown_max_bytes
        return size_limit_for(
            format,
            binary_max_bytes=self.pdf_max_bytes,
            text_max_bytes=self.html_markdown_max_bytes,
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

    def _media_type(self, content_type: str) -> str:
        format = self.formats.for_content_type(content_type)
        if format is None:
            raise DomainError("SOURCE_UNSUPPORTED", "不支持的文档类型", 400, False)
        return format.media_type

    async def _read_limited(self, response: httpx.Response) -> tuple[str, bytes]:
        declared_media_type = self._media_type(response.headers.get("content-type", ""))
        declared_format = self.formats.for_media_type(declared_media_type)
        signed_formats = self.formats.binary_formats()
        # Longest magic first, and a prefix buffer as long as the longest one:
        # a shorter signature that prefixes another must not be matched early.
        signature_length = max((len(format.magic or b"") for format in signed_formats), default=0)

        content_length = response.headers.get("content-length")
        declared_size: int | None = None
        # The widest ceiling applies until the payload identifies its format.
        widest_limit = max(self.pdf_max_bytes, self.html_markdown_max_bytes)
        if content_length:
            try:
                declared_size = int(content_length)
                if declared_size > widest_limit:
                    raise DomainError("DOCUMENT_TOO_LARGE", "文档超过大小限制", 413, False)
            except ValueError:
                pass
        chunks: list[bytes] = []
        size = 0
        prefix = b""
        media_type: str | None = None
        async for chunk in response.aiter_bytes():
            next_size = size + len(chunk)
            if next_size > widest_limit:
                raise DomainError("DOCUMENT_TOO_LARGE", "文档超过大小限制", 413, False)
            prefix = (prefix + chunk)[:signature_length] if signature_length else b""
            if media_type is None and signature_length:
                matched = next(
                    (format for format in signed_formats if prefix.startswith(format.magic or b"")),
                    None,
                )
                if matched is not None:
                    media_type = matched.media_type
                elif not any((format.magic or b"").startswith(prefix) for format in signed_formats):
                    # The prefix can no longer grow into any known signature, so
                    # the body is not a binary format: trust what was declared,
                    # and reject it if the declared format needed a signature.
                    media_type = declared_media_type
                    if declared_format is not None and declared_format.magic is not None:
                        raise DomainError(
                            "SOURCE_UNSUPPORTED",
                            f"{declared_format.name.upper()} 文件签名无效",
                            400,
                            False,
                        )
                if media_type is not None and media_type != declared_media_type:
                    raise DomainError(
                        "SOURCE_UNSUPPORTED", "文档类型与文件签名不匹配", 400, False
                    )
            if media_type is not None:
                max_bytes = self._limit_for(media_type)
                if declared_size is not None and declared_size > max_bytes:
                    raise DomainError("DOCUMENT_TOO_LARGE", "文档超过大小限制", 413, False)
                if next_size > max_bytes:
                    raise DomainError("DOCUMENT_TOO_LARGE", "文档超过大小限制", 413, False)
            size = next_size
            chunks.append(chunk)

        media_type = media_type or declared_media_type
        if (
            declared_format is not None
            and declared_format.magic is not None
            and not prefix.startswith(declared_format.magic)
        ):
            raise DomainError(
                "SOURCE_UNSUPPORTED", f"{declared_format.name.upper()} 文件签名无效", 400, False
            )
        if media_type != declared_media_type:
            raise DomainError("SOURCE_UNSUPPORTED", "文档类型与文件签名不匹配", 400, False)
        max_bytes = self._limit_for(media_type)
        if declared_size is not None and declared_size > max_bytes:
            raise DomainError("DOCUMENT_TOO_LARGE", "文档超过大小限制", 413, False)
        if size > max_bytes:
            raise DomainError("DOCUMENT_TOO_LARGE", "文档超过大小限制", 413, False)
        return media_type, b"".join(chunks)

    def _validate_payload(self, media_type: str, raw_bytes: bytes) -> None:
        format = self.formats.for_media_type(media_type)
        if format is None or format.validate is None:
            return
        problem = format.validate(raw_bytes)
        if problem is not None:
            raise DomainError("SOURCE_UNSUPPORTED", problem, 400, False)

    @staticmethod
    def _title_from_url(source_url: str) -> str:
        path = httpx.URL(source_url).path.rstrip("/")
        return path.rsplit("/", 1)[-1] or "document"
