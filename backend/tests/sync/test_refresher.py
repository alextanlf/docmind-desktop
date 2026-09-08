from __future__ import annotations

import pytest

from app.config import EmbeddingSettings
from app.core.embedding import FakeEmbeddingProvider
from app.document.chunker import SemanticChunker
from app.document.parser import DocumentParser
from app.storage.database import Database
from app.storage.repositories import DocumentStore, RepositoryStore
from app.sync.refresher import DocumentRefresher


class RecordingVectorStore:
    def __init__(self) -> None:
        self.upserts: list[tuple[str, list[str]]] = []
        self.deletes: list[tuple[str, list[str]]] = []

    def upsert(self, repository_id, ids, texts, embeddings, metadatas) -> None:  # type: ignore[no-untyped-def]
        self.upserts.append((repository_id, list(ids)))

    def delete(self, repository_id, ids) -> None:  # type: ignore[no-untyped-def]
        self.deletes.append((repository_id, list(ids)))


def _refresher(database: Database) -> tuple[DocumentRefresher, str]:
    repository = RepositoryStore(database).upsert_remote(
        yuque_id="yuque-1", name="SwiftUI", description=None, yuque_url=None
    )
    refresher = DocumentRefresher(
        document_store=DocumentStore(database),
        parser=DocumentParser(),
        chunker=SemanticChunker(),
        embedding_provider=FakeEmbeddingProvider(
            EmbeddingSettings(model_name="bge", dimension=768)
        ),
        vector_store=RecordingVectorStore(),
    )
    return refresher, repository.id


@pytest.mark.asyncio
async def test_refresher_upserts_and_indexes_remote_document(database: Database) -> None:
    refresher, repository_id = _refresher(database)

    local_id = await refresher.upsert_from_remote(
        repository_id, "doc-1", "State", "# State\n\nbody text"
    )

    document = DocumentStore(database).get(local_id)
    assert document is not None
    assert document.yuque_id == "doc-1"
    assert document.title == "State"
    assert document.content_hash is not None
    assert DocumentStore(database).list_chunks(local_id)


@pytest.mark.asyncio
async def test_refresher_marks_remote_deleted(database: Database) -> None:
    refresher, repository_id = _refresher(database)

    local_id = await refresher.upsert_from_remote(repository_id, "doc-1", "State", "# State")
    await refresher.mark_remote_deleted(repository_id, "doc-1")

    assert DocumentStore(database).get(local_id).remote_deleted is True
