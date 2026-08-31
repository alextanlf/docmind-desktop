from __future__ import annotations

import pytest

from app.config import EmbeddingSettings, VectorStoreSettings
from app.core.embedding import FakeEmbeddingProvider
from app.core.retrieval import HybridRetriever, reciprocal_rank_fusion
from app.storage.models import DocumentChunkRecord, DocumentRecord, RepositoryRecord
from app.storage.vectorstore import PersistentVectorStore


def test_rrf_merges_and_deduplicates_vector_and_bm25_hits() -> None:
    result = reciprocal_rank_fusion(
        vector_ids=["a", "b", "c"], keyword_ids=["b", "d", "a"], k=60
    )

    assert [item.chunk_id for item in result] == ["b", "a", "d", "c"]
    assert result[0].score > result[2].score


def test_hybrid_retriever_uses_default_threshold_without_reloading_app_settings(database, tmp_path) -> None:
    retriever = HybridRetriever(
        database=database,
        vector_store=PersistentVectorStore(VectorStoreSettings(directory=tmp_path / "vectors")),
        embedding_provider=FakeEmbeddingProvider(EmbeddingSettings(dimension=8)),
    )

    assert retriever.similarity_threshold == 0.65


@pytest.fixture
def low_retriever(database, tmp_path) -> HybridRetriever:
    repository = RepositoryRecord(id="repo-1", name="Repository")
    document = DocumentRecord(id="doc-1", repository_id=repository.id, title="Guide")
    chunk = DocumentChunkRecord(
        id="chunk-1",
        document_id=document.id,
        repository_id=repository.id,
        chunk_index=0,
        text="State management guide",
        token_count=3,
        section_path="State",
    )
    with database.session() as session:
        session.add_all([repository, document, chunk])
    store = PersistentVectorStore(VectorStoreSettings(directory=tmp_path / "vectors"))
    store.upsert(
        repository.id,
        [chunk.id],
        [chunk.text],
        [[1.0] + [0.0] * 7],
        [{"doc_id": document.id, "doc_title": document.title, "section_path": "State"}],
    )
    return HybridRetriever(
        database=database,
        vector_store=store,
        embedding_provider=FakeEmbeddingProvider(EmbeddingSettings(dimension=8)),
        similarity_threshold=0.65,
    )


async def test_hybrid_search_returns_insufficient_context_when_score_is_low(
    low_retriever: HybridRetriever,
) -> None:
    result = await low_retriever.search("不存在的 API", ["repo-1"])

    assert result.hits == []
    assert result.max_score < 0.65


async def test_hybrid_search_returns_database_backed_fused_hits(database, tmp_path) -> None:
    repository = RepositoryRecord(id="repo-1", name="Repository")
    document = DocumentRecord(
        id="doc-1", repository_id=repository.id, title="SwiftUI State", source_url="https://example.test"
    )
    chunk = DocumentChunkRecord(
        id="chunk-1",
        document_id=document.id,
        repository_id=repository.id,
        chunk_index=0,
        text="@State manages local state.",
        token_count=5,
        section_path="State",
        source_url="https://example.test",
    )
    with database.session() as session:
        session.add_all([repository, document, chunk])
    provider = FakeEmbeddingProvider(EmbeddingSettings(dimension=8))
    store = PersistentVectorStore(VectorStoreSettings(directory=tmp_path / "vectors"))
    store.upsert(
        repository.id,
        [chunk.id],
        [chunk.text],
        [await provider.embed_query(chunk.text)],
        [{"doc_id": document.id, "doc_title": document.title, "section_path": "State"}],
    )
    result = await HybridRetriever(
        database=database, vector_store=store, embedding_provider=provider, similarity_threshold=0.65
    ).search("@State", [repository.id])

    assert [hit.chunk_id for hit in result.hits] == [chunk.id]
    assert result.hits[0].document_title == "SwiftUI State"
    assert result.hits[0].source_url == "https://example.test"
