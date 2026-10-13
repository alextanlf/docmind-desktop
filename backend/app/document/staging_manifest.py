from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.api.errors import DomainError
from app.document.builtin_formats import builtin_registry
from app.document.formats import FormatRegistry, size_limit_for
from app.document.path_tokens import DIRECTORY_DOMAIN, path_token_matches

_SHA256 = r"^[0-9a-f]{64}$"
_MAX_MANIFEST_BYTES = 2 * 1024 * 1024
_MAX_FILES = 1000
_MAX_TOTAL_BYTES = 2 * 1024**3
_MAX_TEXT_BYTES = 20 * 1024 * 1024
_MAX_PDF_BYTES = 100 * 1024 * 1024


def _changed(message: str = "暂存目录内容已变化") -> DomainError:
    return DomainError("BATCH_SOURCE_CHANGED", message, 409, False)


class ManifestFile(BaseModel):
    relative_path: str = Field(alias="relativePath", min_length=1, max_length=4096)
    staged_id: str = Field(alias="stagedId", min_length=1, max_length=128)
    media_type: str = Field(alias="mediaType")
    size_bytes: int = Field(alias="sizeBytes", ge=0)
    sha256: str = Field(pattern=_SHA256, min_length=64, max_length=64)
    ordinal: int | None = Field(default=None, ge=0, le=_MAX_FILES)

    model_config = {"populate_by_name": True, "extra": "forbid"}

    @field_validator("staged_id")
    @classmethod
    def _require_uuid_staged_id(cls, value: str) -> str:
        try:
            parsed = UUID(value)
        except (TypeError, ValueError, AttributeError):
            raise ValueError("stagedId must be a UUID") from None
        if str(parsed) != value.lower():
            raise ValueError("stagedId must be a canonical UUID")
        return value.lower()


class StagingManifest(BaseModel):
    root_id: str = Field(alias="rootId", min_length=1, max_length=255)
    files: list[ManifestFile] = Field(default_factory=list, max_length=_MAX_FILES)
    #: 链接型集合：字节在用户磁盘上的这个目录里，**一份都不复制进暂存**。
    #:
    #: 与 `root_token` 一起出现（主进程对目录路径的签名）。没有它，清单里每一条的字节就
    #: 来自 `collections/<id>/items/<stagedId>`（拷贝型，今天的行为）。
    #: 两种形态互斥：混着用等于一半的字节来自用户目录、一半来自我们的副本，
    #: 而"这份清单代表什么"就说不清了 —— `validate_manifest` 会直接拒绝。
    root_path: str | None = Field(default=None, alias="rootPath", max_length=4096)
    root_token: str | None = Field(default=None, alias="rootToken", max_length=255)

    model_config = {"populate_by_name": True, "extra": "forbid"}


@dataclass(frozen=True)
class FileSnapshot:
    """Bytes and metadata read from one securely opened staged-file descriptor."""

    digest: str
    size: int
    identity: tuple[int, int]
    raw_bytes: bytes | None = None


def _path_identity(path: Path) -> tuple[int, int]:
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except OSError:
        raise _changed("暂存文件不可用") from None
    if not stat.S_ISREG(metadata.st_mode):
        raise _changed("暂存文件不可用")
    return metadata.st_dev, metadata.st_ino


def read_file_snapshot(
    path: Path,
    *,
    expected_size: int | None = None,
    expected_hash: str | None = None,
    max_bytes: int | None = None,
    collect_bytes: bool = False,
) -> FileSnapshot:
    """Read and verify one regular file without following symlinks.

    The descriptor remains the source of truth for all bytes and metadata.  A
    no-follow path identity check before and after the read detects an atomic
    replacement while the descriptor is being consumed.
    """
    path = Path(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        raise _changed("暂存文件不可用") from None
    try:
        initial = os.fstat(descriptor)
        if not stat.S_ISREG(initial.st_mode):
            raise _changed("暂存文件不可用")
        initial_identity = (initial.st_dev, initial.st_ino)
        if expected_size is not None and initial.st_size != expected_size:
            raise _changed("暂存文件已变化")
        if max_bytes is not None and initial.st_size > max_bytes:
            raise _changed("暂存文件超过大小限制")
        if _path_identity(path) != initial_identity:
            raise _changed("暂存文件已变化")

        digest = hashlib.sha256()
        chunks: list[bytes] = []
        size = 0
        read_limit = (max_bytes + 1) if max_bytes is not None else 64 * 1024
        while True:
            amount = min(64 * 1024, read_limit - size) if max_bytes is not None else 64 * 1024
            if amount <= 0:
                raise _changed("暂存文件超过大小限制")
            chunk = os.read(descriptor, amount)
            if not chunk:
                break
            digest.update(chunk)
            if collect_bytes:
                chunks.append(chunk)
            size += len(chunk)
            if max_bytes is not None and size > max_bytes:
                raise _changed("暂存文件超过大小限制")

        final = os.fstat(descriptor)
        final_identity = (final.st_dev, final.st_ino)
        if (
            not stat.S_ISREG(final.st_mode)
            or final_identity != initial_identity
            or final.st_size != size
            or (expected_size is not None and size != expected_size)
            or (expected_hash is not None and digest.hexdigest() != expected_hash)
            or _path_identity(path) != initial_identity
        ):
            raise _changed("暂存文件已变化")
        value = digest.hexdigest()
        return FileSnapshot(value, size, initial_identity, b"".join(chunks) if collect_bytes else None)
    except DomainError:
        raise
    except OSError:
        raise _changed("暂存文件不可用") from None
    finally:
        os.close(descriptor)


def load_manifest(path: Path, *, max_bytes: int = _MAX_MANIFEST_BYTES) -> StagingManifest:
    """Read and parse an Electron-produced manifest through a bounded descriptor."""
    path = Path(path)
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
    except OSError:
        raise _changed("暂存清单不可用") from None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > max_bytes:
            raise _changed("暂存清单不可用")
        raw = bytearray()
        while True:
            chunk = os.read(descriptor, min(64 * 1024, max_bytes + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > max_bytes:
                raise _changed("暂存清单不可用")
    except DomainError:
        raise
    except OSError:
        raise _changed("暂存清单不可用") from None
    finally:
        os.close(descriptor)
    try:
        value: Any = json.loads(bytes(raw))
        return StagingManifest.model_validate(value)
    except (json.JSONDecodeError, TypeError, ValueError, ValidationError):
        raise _changed("暂存清单格式无效") from None


def _normalise_relative(value: str) -> str:
    if "\\" in value or value.startswith("/"):
        raise _changed("暂存清单路径无效")
    path = PurePosixPath(value)
    parts = path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts) or str(path) != value:
        raise _changed("暂存清单路径无效")
    return "/".join(parts)


def _hash_and_size(path: Path, expected_size: int, expected_hash: str) -> str:
    """Verify a staged regular file and return its digest immediately before use."""
    return read_file_snapshot(
        path,
        expected_size=expected_size,
        expected_hash=expected_hash,
        max_bytes=max(expected_size, 1),
    ).digest


def verify_file_snapshot(path: Path, snapshot: FileSnapshot) -> None:
    """Reject a path that was replaced after a descriptor snapshot was read."""
    if _path_identity(Path(path)) != snapshot.identity:
        raise _changed("暂存文件已变化")


def linked_directory_root(manifest: StagingManifest, secret: str | None) -> Path | None:
    """链接型集合里，用户授权的那个目录（已验签、已存在）；拷贝型返回 ``None``。

    这是目录批次上的**能力检查**：签名证明"用户确实选过这个目录"（见
    :mod:`app.document.path_tokens`）。后端因此只读得过授权的目录，
    而渲染层造不出一个可用的授权。
    """
    if manifest.root_path is None:
        return None
    if secret is None:
        # 没有密钥就无从判断这个目录是不是被授权的 —— 拒绝，而不是默认信任。
        raise _changed("目录授权无法校验")
    if not path_token_matches(secret, DIRECTORY_DOMAIN, manifest.root_path, manifest.root_token):
        raise _changed("目录授权无效")
    root = Path(manifest.root_path)
    if not root.is_absolute():
        raise _changed("目录授权无效")
    try:
        resolved = root.resolve(strict=True)
    except OSError:
        raise _changed("授权的目录不可用") from None
    if not resolved.is_dir():
        raise _changed("授权的目录不可用")
    return resolved


def entry_path(
    manifest: StagingManifest,
    collection_root: Path,
    entry: ManifestFile,
    *,
    linked_root: Path | None = None,
) -> Path:
    """清单里这一条记录的字节文件在哪。

    拷贝型在 ``collections/<id>/items/<stagedId>``；链接型在用户目录下的相对路径上。
    **只在这一个地方回答这个问题** —— 校验、发现、导入三处各自拼一次路径，
    迟早会有一处忘记做包含性检查。
    """
    if manifest.root_path is None:
        return collection_root / "items" / str(entry.staged_id)
    root = linked_root if linked_root is not None else linked_directory_root(manifest, None)
    if root is None:  # pragma: no cover - root_path 非空时不会走到
        raise _changed("目录授权无效")
    return root / entry.relative_path


def _verify_entry_bytes(path: Path, entry: ManifestFile, root: Path, *, linked: bool) -> None:
    """读一遍这条记录的字节并核对摘要与大小。

    链接型还要多一步**包含性检查**：清单里的相对路径已经过 `_normalise_relative`
    （拒绝 `..`、反斜杠、绝对路径），但符号链接可以绕过字符串层面的检查 ——
    所以解析之后必须仍然落在授权的目录里。
    """
    if linked:
        try:
            path.resolve().relative_to(root.resolve())
        except (OSError, ValueError):
            raise _changed("目录内文件路径无效") from None
    _hash_and_size(path, entry.size_bytes, entry.sha256)


def validate_manifest(
    manifest: StagingManifest | dict[str, Any],
    collection_root: Path,
    formats: FormatRegistry | None = None,
    *,
    secret: str | None = None,
) -> list[ManifestFile]:
    registry = formats if formats is not None else builtin_registry()
    try:
        parsed = manifest if isinstance(manifest, StagingManifest) else StagingManifest.model_validate(manifest)
    except ValidationError:
        raise _changed("暂存清单格式无效") from None
    root = Path(collection_root).resolve()
    linked_root = linked_directory_root(parsed, secret)
    seen: set[str] = set()
    seen_paths: set[str] = set()
    total_bytes = 0
    validated: list[ManifestFile] = []
    for entry in parsed.files:
        relative_path = _normalise_relative(entry.relative_path)
        try:
            staged_uuid = UUID(entry.staged_id)
        except (TypeError, ValueError, AttributeError):
            raise _changed("暂存文件标识无效") from None
        if str(staged_uuid) != entry.staged_id.lower():
            raise _changed("暂存文件标识无效")
        if entry.staged_id in seen or relative_path in seen_paths:
            raise _changed("暂存清单内容无效")
        format = registry.for_media_type(entry.media_type)
        if format is None:
            raise _changed("暂存清单内容无效")
        max_bytes = size_limit_for(
            format, binary_max_bytes=_MAX_PDF_BYTES, text_max_bytes=_MAX_TEXT_BYTES
        )
        if entry.size_bytes > max_bytes:
            raise DomainError("BATCH_LIMIT_EXCEEDED", "目录文件超过大小限制", 413, False)
        total_bytes += entry.size_bytes
        if total_bytes > _MAX_TOTAL_BYTES:
            raise DomainError("BATCH_LIMIT_EXCEEDED", "目录文件总大小超过限制", 413, False)
        seen.add(entry.staged_id)
        seen_paths.add(relative_path)
        if linked_root is None:
            staged_path = entry_path(parsed, root, entry)
            try:
                staged_path.resolve().relative_to(root)
            except (OSError, ValueError):
                raise _changed("暂存文件路径无效") from None
        else:
            staged_path = entry_path(parsed, root, entry, linked_root=linked_root)
        _verify_entry_bytes(staged_path, entry, linked_root or root, linked=linked_root is not None)
        validated.append(entry.model_copy(update={"relative_path": relative_path}))
    return validated


def sha256_file(path: Path) -> str:
    """Hash a regular file without following symlinks."""
    return read_file_snapshot(path).digest
