from __future__ import annotations

import pytest

from app.api.errors import DomainError
from app.config import VectorStoreSettings
from app.storage.vectorstore import PersistentVectorStore


@pytest.fixture
def vector_store(tmp_path) -> PersistentVectorStore:
    return PersistentVectorStore(VectorStoreSettings(directory=tmp_path / "vectors"))


def test_vector_store_round_trip(vector_store: PersistentVectorStore) -> None:
    vector_store.upsert(
        "repo-1",
        ["chunk-1"],
        ["Virtual Thread"],
        [[1.0, 0.0]],
        [{"doc_id": "doc-1", "doc_title": "Java", "section_path": "线程"}],
    )

    hits = vector_store.query("repo-1", [1.0, 0.0], top_k=1)

    assert hits[0].id == "chunk-1"
    assert hits[0].metadata["doc_title"] == "Java"
    assert hits[0].similarity == 1.0


def test_vector_store_persists_and_normalizes_repository_collection_name(tmp_path) -> None:
    settings = VectorStoreSettings(directory=tmp_path / "vectors")
    first = PersistentVectorStore(settings)
    first.upsert(
        "repo/id with spaces",
        ["chunk-1"],
        ["text"],
        [[1.0, 0.0]],
        [{"doc_id": "doc-1", "doc_title": "Title"}],
    )

    second = PersistentVectorStore(settings)
    assert second.collection_name("repo/id with spaces") == "repo_repo_id_with_spaces"
    assert second.query("repo/id with spaces", [1.0, 0.0], top_k=1)[0].id == "chunk-1"


def test_vector_store_returns_low_similarity_candidates_and_delete_is_idempotent(
    vector_store: PersistentVectorStore,
) -> None:
    vector_store.upsert(
        "repo-1",
        ["chunk-1"],
        ["text"],
        [[1.0, 0.0]],
        [{"doc_id": "doc-1", "doc_title": "Title", "ignored": "discard"}],
    )

    low_confidence = vector_store.query("repo-1", [0.0, 1.0], top_k=1)
    assert [(hit.id, hit.similarity) for hit in low_confidence] == [("chunk-1", 0.0)]
    vector_store.delete("repo-1", ["missing", "chunk-1"])
    vector_store.delete("repo-1", ["chunk-1"])
    assert vector_store.query("repo-1", [1.0, 0.0], top_k=1) == []


def test_vector_store_rejects_empty_repository_id(vector_store: PersistentVectorStore) -> None:
    with pytest.raises(DomainError, match="仓库") as error:
        vector_store.collection_name("")

    assert error.value.code == "INDEX_FAILED"


def test_vector_store_maps_chroma_read_failures_to_index_failed(
    vector_store: PersistentVectorStore, monkeypatch
) -> None:
    def fail_get_collection(name: str):
        del name
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(vector_store.client, "get_collection", fail_get_collection)

    with pytest.raises(DomainError) as error:
        vector_store.query("repo-1", [1.0, 0.0], top_k=1)

    assert error.value.code == "INDEX_FAILED"
