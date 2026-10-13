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
from app.document.builtin_formats import builtin_registry
from app.document.formats import FormatRegistry, size_limit_for
from app.document.path_tokens import FILE_DOMAIN, path_token_matches
from app.document.staging_manifest import (
    ManifestFile,
    StagingManifest,
    entry_path,
    linked_directory_root,
    load_manifest,
    read_file_snapshot,
    validate_manifest,
    verify_file_snapshot,
)
from app.schemas.batches import CachedSourceRef
from app.schemas.imports import DownloadedDocument, SourcePreview, SourceRef

if TYPE_CHECKING:
    from app.document.downloader import DocumentDownloader


def _unsupported(message: str = "不支持的文档来源") -> DomainError:
    return DomainError("SOURCE_UNSUPPORTED", message, 400, False)


#: 控制字符集合，按「字符」而不是字节判，免得把一个多字节字符劈成两半。
_CONTROL_CHARACTERS = {chr(code) for code in (*range(32), 127)}


def display_title(name: str | None) -> str | None:
    """把客户端上报的文件名收敛成一个能安全当标题用的显示值。

    这个名字会一路进预览、文档标题、chunk 与聊天引用，所以按「不可信的显示输入」对待：
    只取 basename（客户端在 Windows 上给的是反斜杠路径），丢掉控制字符，压平空白，
    截短到一个能安全过 IPC 的长度。**不保留任何路径成分** —— 暂存绝对路径不许出现在
    引用里这条约束没有变，变的只是「用户自己给文件起的名字」可以出现了。

    截断取 240 而不是标题列的 1024：客户端 `SourcePreviewSchema.title` 是 `text(240)`，
    超过它会在 IPC 边界被拒，用户看到的是「导入失败」而不是一个长标题。
    """
    if not isinstance(name, str):
        return None
    candidate = name.replace("\\", "/").rsplit("/", 1)[-1]
    flattened = "".join(
        " " if character in _CONTROL_CHARACTERS else character for character in candidate
    )
    cleaned = " ".join(flattened.split()).strip()
    if not cleaned or cleaned in {".", ".."}:
        return None
    return cleaned[:240]


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


def _validated_staged_path(staging_dir: Path, staged_id: str) -> Path:
    """定位并校验一个单文件暂存副本；任何一种不可用都抛 ``SOURCE_UNSUPPORTED``。

    这里做的是**安全**检查：id 必须是规范 UUID、目录里必须唯一匹配、解析后必须还在
    暂存目录内、而且必须是普通文件。加载与清扫共用这一份 —— 两边各自实现一遍，
    迟早会有一边忘了防符号链接逃逸。
    """
    try:
        canonical_id = str(UUID(staged_id))
    except (TypeError, ValueError, AttributeError):
        raise _unsupported() from None
    if canonical_id != staged_id.lower():
        raise _unsupported()

    root = staging_dir.resolve()
    matches = [path for path in staging_dir.glob(f"{canonical_id}.*") if path.parent == staging_dir]
    if len(matches) != 1:
        raise _unsupported("暂存文件不存在或不唯一")
    resolved = matches[0].resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise _unsupported("暂存文件不在允许目录内") from None
    if not resolved.is_file():
        raise _unsupported("暂存文件不可用")
    return resolved


def locate_staged_file(staging_dir: Path, staged_id: str) -> Path | None:
    """按 staged id 定位单文件暂存副本，找不到或不唯一时返回 ``None``。

    **刻意不查格式注册表**：清扫路径不能靠注册表判断"这是不是一个暂存文件" —— 一个插件
    格式（`.pages`）在 builtin 注册表里查不到，会被误判成外来文件而永远留着。格式检查
    只对"要解析它"的调用者有约束，所以它留在 :class:`StagedFileStore` 里。
    """
    try:
        return _validated_staged_path(staging_dir, staged_id)
    except DomainError:
        return None


def discard_staged_file(staging_dir: Path, staged_id: str) -> bool:
    """删掉一个单文件暂存副本；返回是否真的删掉了。

    删除失败只返回 ``False``：清扫是尽力而为的维护动作，不该让调用它的导入流程失败 ——
    最坏的结果是这份副本留到下一次清扫。
    """
    path = locate_staged_file(staging_dir, staged_id)
    if path is None:
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


class StagedFileStore:
    def __init__(self, settings: AppSettings, formats: FormatRegistry | None = None) -> None:
        self.staging_dir = settings.staging_dir
        self.html_markdown_max_bytes = settings.html_markdown_max_bytes
        self.pdf_max_bytes = settings.pdf_max_bytes
        self.formats = formats if formats is not None else builtin_registry()

    def resolve(self, staged_id: str) -> Path:
        path = _validated_staged_path(self.staging_dir, staged_id)
        if self.formats.for_pickable_extension(path.suffix) is None:
            raise _unsupported()
        return path

    def load(self, staged_id: str, display_name: str | None = None) -> DownloadedDocument:
        path = self.resolve(staged_id)
        format = self.formats.for_pickable_extension(path.suffix)
        if format is None:
            raise _unsupported()
        size_limit = size_limit_for(
            format,
            binary_max_bytes=self.pdf_max_bytes,
            text_max_bytes=self.html_markdown_max_bytes,
        )
        raw_bytes = self._read_limited(path, size_limit)
        if format.validate is not None:
            problem = format.validate(raw_bytes)
            if problem is not None:
                raise _unsupported(problem)
        return DownloadedDocument(
            title=display_title(display_name) or path.name,
            # Preserve only the opaque staged identifier across the backend
            # boundary; never expose the absolute staging path in previews,
            # persisted chunks, or later chat citations.
            source_url=f"staged://{staged_id.lower()}",
            media_type=format.media_type,
            raw_bytes=raw_bytes,
            local_path=path,
        )

    @staticmethod
    def _read_limited(path: Path, size_limit: int) -> bytes:
        return _read_bounded(
            path, size_limit, follow_symlinks=False, unavailable="暂存文件不可用"
        )


def _read_bounded(
    path: Path, size_limit: int, *, follow_symlinks: bool, unavailable: str
) -> bytes:
    """把一个文件读成字节，带上限与"必须是普通文件"的检查。

    上限是配置里的绝对值，不是"相信调用方"：**先 fstat 描述符再读**，因此检查作用于真正
    被打开的那个文件，而不是某个可能已经换掉的路径（TOCTOU）。
    读的过程中再数一遍，防止文件在读完之前长大。

    ``follow_symlinks`` 是两类调用者唯一的区别：暂存目录里的东西是**别人可能放过的**，
    所以拒绝符号链接（`O_NOFOLLOW`）；而链接型原件是**用户自己在对话框里选的路径**，
    他选一个指向别处的链接就该读那个目标 —— 签名保护的也正是他选的那串路径。
    """
    flags = os.O_RDONLY | (0 if follow_symlinks else getattr(os, "O_NOFOLLOW", 0))
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise _unsupported(unavailable) from None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise _unsupported(unavailable)
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


class LinkedFileStore:
    """读用户磁盘上的原件 —— 不留副本，只把路径记下来。

    **签名是这里唯一的门。** 一个没有签名（或签名对不上）的路径一律
    ``SOURCE_UNSUPPORTED``，而且**不区分"路径不存在"与"签名不对"**：分化这两种回答会让
    这个接口变成一个任意路径的存在性探测器。签名由主进程出，密钥是它注入后端的
    ``session_token``（见 :mod:`app.document.path_tokens`）。
    """

    def __init__(self, settings: AppSettings, formats: FormatRegistry | None = None) -> None:
        self.secret = settings.session_token.get_secret_value()
        self.html_markdown_max_bytes = settings.html_markdown_max_bytes
        self.pdf_max_bytes = settings.pdf_max_bytes
        self.formats = formats if formats is not None else builtin_registry()

    def load(
        self, value: str, token: str | None, display_name: str | None = None
    ) -> DownloadedDocument:
        if not path_token_matches(self.secret, FILE_DOMAIN, value, token):
            raise _unsupported()
        path = Path(value)
        # 必须是绝对路径：主进程给的永远是对话框返回的绝对路径，相对路径说明请求不是它发的。
        if not path.is_absolute():
            raise _unsupported()
        format = self.formats.for_pickable_extension(path.suffix)
        if format is None:
            raise _unsupported()
        size_limit = size_limit_for(
            format,
            binary_max_bytes=self.pdf_max_bytes,
            text_max_bytes=self.html_markdown_max_bytes,
        )
        raw_bytes = _read_bounded(
            path, size_limit, follow_symlinks=True, unavailable="原件不可读"
        )
        if format.validate is not None:
            problem = format.validate(raw_bytes)
            if problem is not None:
                raise _unsupported(problem)
        return DownloadedDocument(
            title=display_title(display_name) or path.name,
            # 与暂存路径同一条约束：**绝对路径不许进预览、chunk 或引用**。
            # 这里只带用户看得见的文件名。
            source_url=f"linked://{display_title(display_name) or path.name}",
            media_type=format.media_type,
            raw_bytes=raw_bytes,
            source_path=path,
        )


def load_stored_original(
    path: Path,
    *,
    formats: FormatRegistry,
    binary_max_bytes: int,
    text_max_bytes: int,
    title: str | None = None,
) -> DownloadedDocument:
    """读一份**已经绑定在文档记录里**的原件（重新转换用）。

    与 :meth:`LinkedFileStore.load` 的区别是这里**不验签**，因为路径不是这次请求带上来的，
    而是数据库里存着的那一个 —— 它当初能存进去，就已经过了一次签名（导入或重新定位）。
    换句话说：能力在"绑定"那一刻就授予了，到期时间就是这条绑定本身。

    **不要拿它去读请求里的路径。** 那样做等于把刚刚那道门拆掉。

    ``title`` 是"解析器没能从文件里读出标题时"的兜底，``None`` 表示用文件名 —— 链接型
    就该用文件名（那是用户自己起的）。但拷贝型的名字（``raw.docx``）是我们起的，
    拿它当标题等于把文档改名叫 raw.docx。
    """
    format = formats.for_pickable_extension(path.suffix)
    if format is None:
        raise _unsupported()
    size_limit = size_limit_for(
        format, binary_max_bytes=binary_max_bytes, text_max_bytes=text_max_bytes
    )
    raw_bytes = _read_bounded(path, size_limit, follow_symlinks=True, unavailable="原件不可读")
    if format.validate is not None:
        problem = format.validate(raw_bytes)
        if problem is not None:
            raise _unsupported(problem)
    return DownloadedDocument(
        title=title or path.name,
        source_url=f"linked://{path.name}",
        media_type=format.media_type,
        raw_bytes=raw_bytes,
        source_path=path,
    )


class CollectionCacheStore:
    """Internal reader for immutable Phase 2 collection snapshots."""

    def __init__(self, settings: AppSettings, formats: FormatRegistry | None = None) -> None:
        self.staging_dir = settings.staging_dir
        self.manifest_max_bytes = settings.staging_manifest_max_bytes
        self.html_markdown_max_bytes = settings.html_markdown_max_bytes
        self.pdf_max_bytes = settings.pdf_max_bytes
        # 链接型集合适用的目录授权校验密钥（见 path_tokens）。
        self.secret = settings.session_token.get_secret_value()
        self.formats = formats if formats is not None else builtin_registry()

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
        entries = validate_manifest(manifest, collection_root, secret=self.secret)
        linked_root = linked_directory_root(manifest, self.secret)
        entries_by_id = {entry.staged_id: entry for entry in entries}
        loaded: dict[str, DownloadedDocument] = {}
        for request in requests:
            cache_id = str(request.cache_ref.cache_id)
            entry = entries_by_id.get(cache_id)
            if entry is None:
                raise _batch_source_changed("暂存文件不在清单中")
            self._validate_descriptor(manifest, entry, request)
            loaded[request.key] = self._load_entry(
                manifest,
                collection_root,
                canonical_collection_id,
                entry,
                request,
                linked_root=linked_root,
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
        manifest: StagingManifest,
        collection_root: Path,
        collection_id: str,
        entry: ManifestFile,
        request: CollectionCacheRequest,
        *,
        linked_root: Path | None = None,
    ) -> DownloadedDocument:
        # 路径只由 `entry_path` 回答 —— 校验、发现、导入三处各拼一次，
        # 迟早会有一处忘记做包含性检查。
        path = entry_path(manifest, collection_root, entry, linked_root=linked_root)
        if linked_root is not None:
            try:
                path.resolve().relative_to(linked_root)
            except (OSError, ValueError):
                raise _batch_source_changed("目录内文件路径无效") from None
        format = self.formats.for_media_type(entry.media_type)
        if format is None:
            raise _batch_source_changed("暂存文件类型无效")
        size_limit = size_limit_for(
            format,
            binary_max_bytes=self.pdf_max_bytes,
            text_max_bytes=self.html_markdown_max_bytes,
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
        if format.validate is not None:
            problem = format.validate(raw_bytes)
            if problem is not None:
                raise _batch_source_changed(problem)
        return DownloadedDocument(
            title=request.title,
            source_url=f"staged-collection://{collection_id}/{entry.staged_id}",
            media_type=entry.media_type,
            raw_bytes=raw_bytes,
            local_path=path,
            # 链接型集合的字节就是**用户磁盘上的那个文件**：把归属一并交出去，导入那边才会
            # 记 source_path（否则会把它当成我们的副本，给 PDF 再写一份 raw.bin），
            # 后续"原件被改过吗"也才有依据。
            #
            # 少了这一行不会有任何报错 —— 只会静默地多存一份副本，并且永远显示不出变更。
            source_path=path if linked_root is not None else None,
        )


class SourceInspector:
    def __init__(
        self,
        settings: AppSettings,
        downloader: DocumentDownloader | None = None,
        formats: FormatRegistry | None = None,
    ) -> None:
        registry = formats if formats is not None else builtin_registry()
        self.formats = registry
        self.staged_store = StagedFileStore(settings, registry)
        self.linked_store = LinkedFileStore(settings, registry)
        self.collection_store = CollectionCacheStore(settings, registry)
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
        if ref.kind == "linked_file":
            return self.linked_store.load(ref.value, ref.path_token, ref.name)
        return self.staged_store.load(ref.value, ref.name)

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
            format = self.formats.for_media_type(cache_ref.media_type)
            if format is None:
                raise DomainError("SOURCE_NOT_FOUND", "远程缓存类型无效", 404, False)
            size_limit = size_limit_for(
                format,
                binary_max_bytes=self.collection_store.pdf_max_bytes,
                text_max_bytes=self.collection_store.html_markdown_max_bytes,
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
