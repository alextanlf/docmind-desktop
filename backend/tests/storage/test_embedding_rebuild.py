from __future__ import annotations

from app.storage.database import Database
from app.storage.repositories import EmbeddingRebuildStore


def test_embedding_rebuild_marker_roundtrip(database: Database) -> None:
    store = EmbeddingRebuildStore(database)
    assert store.needs_rebuild("doc-1") is False

    store.mark_needs_rebuild("doc-1")
    assert store.needs_rebuild("doc-1") is True

    store.clear("doc-1")
    assert store.needs_rebuild("doc-1") is False
