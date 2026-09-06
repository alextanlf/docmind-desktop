from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest

from app.api.errors import DomainError
from app.document import discovery as discovery_module
from app.document.discovery import DirectoryDiscovery, read_cached_source
from app.schemas.batches import CachedSourceRef, DiscoveryRequest


@pytest.fixture
def staging_root(tmp_path: Path) -> Path:
    root = tmp_path / "staging"
    collection = root / "collections" / "c1" / "items"
    collection.mkdir(parents=True)
    (collection / "00000000-0000-0000-0000-000000000001").write_bytes(b"# Guide\n")
    (collection / "00000000-0000-0000-0000-000000000002").write_text("<html><h1>Index</h1></html>", encoding="utf-8")
    manifest = {
        "rootId": "r1",
        "files": [
            {
                "relativePath": "index.html",
                "stagedId": "00000000-0000-0000-0000-000000000002",
                "mediaType": "text/html",
                "sizeBytes": (collection / "00000000-0000-0000-0000-000000000002").stat().st_size,
                "sha256": hashlib.sha256((collection / "00000000-0000-0000-0000-000000000002").read_bytes()).hexdigest(),
            },
            {
                "relativePath": "docs/a.md",
                "stagedId": "00000000-0000-0000-0000-000000000001",
                "mediaType": "text/markdown",
                "sizeBytes": (collection / "00000000-0000-0000-0000-000000000001").stat().st_size,
                "sha256": hashlib.sha256((collection / "00000000-0000-0000-0000-000000000001").read_bytes()).hexdigest(),
            },
        ],
    }
    (root / "collections" / "c1" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


@pytest.mark.asyncio
async def test_directory_discovery_is_deterministic_and_reports_progress(staging_root: Path) -> None:
    request = DiscoveryRequest(
        batch_id=UUID("00000000-0000-0000-0000-000000000001"),
        source_kind="staged_directory",
        source_descriptor={"collectionId": "c1"},
        repository_id=UUID(int=2),
    )
    events: list[object] = []
    result = await DirectoryDiscovery(staging_root).discover(request, events.append)
    assert [item.source_identity for item in result.sources] == ["folder:r1:docs/a.md", "folder:r1:index.html"]
    assert result.sources[0].title == "Guide"
    assert result.sources[1].title == "index.html"
    assert result.sources[0].source_revision == hashlib.sha256((staging_root / "collections/c1/items/00000000-0000-0000-0000-000000000001").read_bytes()).hexdigest()
    assert all("body" not in event.model_dump() for event in events)


@pytest.mark.asyncio
async def test_directory_discovery_rejects_replacement_between_digest_and_metadata(
    staging_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = staging_root / "collections" / "c1" / "items" / "00000000-0000-0000-0000-000000000001"
    replacement = staging_root / "replacement.md"
    replacement.write_bytes(b"# External bytes\n")
    replaced = False

    original_snapshot = discovery_module.read_file_snapshot

    def replace_then_snapshot(path: Path, **kwargs: object):
        nonlocal replaced
        snapshot = original_snapshot(path, **kwargs)
        if not replaced:
            replaced = True
            target.unlink()
            replacement.rename(target)
        return snapshot

    monkeypatch.setattr(discovery_module, "read_file_snapshot", replace_then_snapshot)
    request = DiscoveryRequest(
        batch_id=UUID("00000000-0000-0000-0000-000000000001"),
        source_kind="staged_directory",
        source_descriptor={"collectionId": "c1"},
        repository_id=UUID(int=2),
    )

    with pytest.raises(DomainError) as error:
        await DirectoryDiscovery(staging_root).discover(request, lambda _event: None)
    assert error.value.code == "BATCH_SOURCE_CHANGED"
    assert replaced


def test_read_cached_source_requires_rooted_snapshot_and_verifies_hash(tmp_path: Path) -> None:
    collection_id = UUID("00000000-0000-0000-0000-000000000010")
    cache_id = UUID("00000000-0000-0000-0000-000000000011")
    root = tmp_path / "staging"
    item = root / "collections" / str(collection_id) / "items" / str(cache_id)
    item.parent.mkdir(parents=True)
    item.write_bytes(b"# A\n")
    ref = CachedSourceRef(
        cache_id=str(cache_id),
        media_type="text/markdown",
        byte_size=99,
        sha256="0" * 64,
    )

    with pytest.raises(DomainError) as missing_root:
        read_cached_source(ref)
    assert missing_root.value.code == "BATCH_SOURCE_CHANGED"

    with pytest.raises(DomainError) as mismatch:
        read_cached_source(ref, root, str(collection_id))
    assert mismatch.value.code == "BATCH_SOURCE_CHANGED"

    symlink_target = root / "outside"
    symlink_target.write_bytes(b"# outside\n")
    item.unlink()
    item.symlink_to(symlink_target)
    valid_ref = ref.model_copy(update={"byte_size": symlink_target.stat().st_size, "sha256": hashlib.sha256(symlink_target.read_bytes()).hexdigest()})
    with pytest.raises(DomainError) as symlink_error:
        read_cached_source(valid_ref, root, str(collection_id))
    assert symlink_error.value.code == "BATCH_SOURCE_CHANGED"
