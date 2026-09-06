from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.api.errors import DomainError
from app.document.staging_manifest import load_manifest, validate_manifest
from app.schemas.batches import CachedSourceRef


@pytest.fixture
def staging_root(tmp_path: Path) -> Path:
    root = tmp_path / "staging"
    collection = root / "collections" / "c1" / "items"
    collection.mkdir(parents=True)
    (collection / "a").write_bytes(b"# A\n")
    return root


def valid_manifest(*, relative_path: str = "docs/a.md") -> dict[str, object]:
    return {
        "rootId": "r1",
        "files": [
            {
                "relativePath": relative_path,
                "stagedId": "00000000-0000-0000-0000-000000000001",
                "mediaType": "text/markdown",
                "sizeBytes": 4,
                "sha256": hashlib.sha256(b"# A\n").hexdigest(),
            }
        ],
    }


@pytest.mark.parametrize("relative_path", ["/tmp/escape.md", "../outside.md", "docs/../a.md", "docs\\a.md"])
def test_manifest_rejects_absolute_or_traversal_paths(staging_root: Path, relative_path: str) -> None:
    manifest = valid_manifest(relative_path=relative_path)
    with pytest.raises(DomainError) as error:
        validate_manifest(manifest, staging_root / "collections" / "c1")
    assert error.value.code == "BATCH_SOURCE_CHANGED"


def test_manifest_rejects_hash_or_size_mismatch(staging_root: Path) -> None:
    manifest = valid_manifest()
    manifest["files"][0]["sizeBytes"] = 3  # type: ignore[index]
    with pytest.raises(DomainError) as error:
        validate_manifest(manifest, staging_root / "collections" / "c1")
    assert error.value.code == "BATCH_SOURCE_CHANGED"


def test_load_manifest_parses_json(staging_root: Path) -> None:
    manifest_path = staging_root / "collections" / "c1" / "manifest.json"
    manifest_path.write_text(json.dumps(valid_manifest()), encoding="utf-8")
    loaded = load_manifest(manifest_path)
    assert loaded.root_id == "r1"
    assert loaded.files[0].staged_id == "00000000-0000-0000-0000-000000000001"


def test_cached_source_ref_rejects_non_uuid_cache_id() -> None:
    with pytest.raises(ValidationError):
        CachedSourceRef(cache_id="not-a-uuid", media_type="text/markdown", byte_size=1, sha256="0" * 64)
