from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest

from app.document.discovery import DirectoryDiscovery
from app.schemas.batches import DiscoveryRequest


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
