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

_SHA256 = r"^[0-9a-f]{64}$"
_MEDIA_TYPES = {"text/markdown", "text/html", "application/pdf"}
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


def validate_manifest(manifest: StagingManifest | dict[str, Any], collection_root: Path) -> list[ManifestFile]:
    try:
        parsed = manifest if isinstance(manifest, StagingManifest) else StagingManifest.model_validate(manifest)
    except ValidationError:
        raise _changed("暂存清单格式无效") from None
    root = Path(collection_root).resolve()
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
        if entry.staged_id in seen or relative_path in seen_paths or entry.media_type not in _MEDIA_TYPES:
            raise _changed("暂存清单内容无效")
        max_bytes = _MAX_PDF_BYTES if entry.media_type == "application/pdf" else _MAX_TEXT_BYTES
        if entry.size_bytes > max_bytes:
            raise DomainError("BATCH_LIMIT_EXCEEDED", "目录文件超过大小限制", 413, False)
        total_bytes += entry.size_bytes
        if total_bytes > _MAX_TOTAL_BYTES:
            raise DomainError("BATCH_LIMIT_EXCEEDED", "目录文件总大小超过限制", 413, False)
        seen.add(entry.staged_id)
        seen_paths.add(relative_path)
        staged_path = root / "items" / str(entry.staged_id)
        try:
            staged_path.resolve().relative_to(root.resolve())
        except ValueError:
            raise _changed("暂存文件路径无效") from None
        _hash_and_size(staged_path, entry.size_bytes, entry.sha256)
        validated.append(entry.model_copy(update={"relative_path": relative_path}))
    return validated


def sha256_file(path: Path) -> str:
    """Hash a regular file without following symlinks."""
    return read_file_snapshot(path).digest
