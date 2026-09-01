from __future__ import annotations

import pytest

from app.config import EmbeddingSettings, VectorStoreSettings
from app.core.embedding import FakeEmbeddingProvider
from app.core.retrieval import HybridRetriever, reciprocal_rank_fusion
from app.storage.models import DocumentChunkRecord, DocumentRecord, RepositoryRecord
from app.storage.vectorstore import PersistentVectorStore, VectorHit


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


async def test_hybrid_search_uses_global_vector_top_ten_across_repositories(database) -> None:
    repositories = [
        RepositoryRecord(id="repo-a", name="Repository A"),
        RepositoryRecord(id="repo-b", name="Repository B"),
    ]
    documents = [
        DocumentRecord(id="doc-a", repository_id="repo-a", title="A"),
        DocumentRecord(id="doc-b", repository_id="repo-b", title="B"),
    ]
    chunks = [
        DocumentChunkRecord(
            id=f"a-{index}",
            document_id="doc-a",
            repository_id="repo-a",
            chunk_index=index,
            text="other",
            token_count=1,
        )
        for index in range(10)
    ]
    chunks.append(
        DocumentChunkRecord(
            id="b-high",
            document_id="doc-b",
            repository_id="repo-b",
            chunk_index=0,
            text="needle",
            token_count=1,
        )
    )
    with database.session() as session:
        session.add_all([*repositories, *documents, *chunks])
    vector_store = _StubVectorStore(
        {
            "repo-a": [_vector_hit(chunk.id, 0.70) for chunk in chunks[:-1]],
            "repo-b": [_vector_hit("b-high", 0.99)],
        }
    )
    retriever = HybridRetriever(
        database=database,
        vector_store=vector_store,  # type: ignore[arg-type]
        embedding_provider=FakeEmbeddingProvider(EmbeddingSettings(dimension=8)),
    )

    result = await retriever.search("needle", ["repo-a", "repo-b"])

    assert result.hits[0].chunk_id == "b-high"
    assert result.max_score == 0.99


async def test_hybrid_search_caps_results_at_five_and_rejects_nonpositive_limits(database) -> None:
    repository = RepositoryRecord(id="repo-1", name="Repository")
    document = DocumentRecord(id="doc-1", repository_id=repository.id, title="Guide")
    chunks = [
        DocumentChunkRecord(
            id=f"chunk-{index}",
            document_id=document.id,
            repository_id=repository.id,
            chunk_index=index,
            text="needle",
            token_count=1,
        )
        for index in range(6)
    ]
    with database.session() as session:
        session.add_all([repository, document, *chunks])
    retriever = HybridRetriever(
        database=database,
        vector_store=_StubVectorStore({"repo-1": [_vector_hit(chunk.id, 0.9) for chunk in chunks]}),
        embedding_provider=FakeEmbeddingProvider(EmbeddingSettings(dimension=8)),
    )

    capped = await retriever.search("needle", [repository.id], top_k=10)
    empty = await retriever.search("needle", [repository.id], top_k=0)

    assert len(capped.hits) == 5
    assert empty.hits == []


async def test_orphan_vectors_do_not_raise_repository_confidence(database) -> None:
    repository = RepositoryRecord(id="repo-1", name="Repository")
    document = DocumentRecord(id="doc-1", repository_id=repository.id, title="Guide")
    chunk = DocumentChunkRecord(
        id="valid-chunk",
        document_id=document.id,
        repository_id=repository.id,
        chunk_index=0,
        text="needle",
        token_count=1,
    )
    with database.session() as session:
        session.add_all([repository, document, chunk])
    retriever = HybridRetriever(
        database=database,
        vector_store=_StubVectorStore(
            {
                repository.id: [
                    _vector_hit("orphan-chunk", 0.99),
                    _vector_hit(chunk.id, 0.40),
                ]
            }
        ),  # type: ignore[arg-type]
        embedding_provider=FakeEmbeddingProvider(EmbeddingSettings(dimension=8)),
        similarity_threshold=0.65,
    )

    result = await retriever.search("needle", [repository.id])

    assert result.hits == []
    assert result.max_score == 0.40


async def test_nonpositive_bm25_scores_are_not_reported_as_keyword_evidence(database) -> None:
    repository = RepositoryRecord(id="repo-1", name="Repository")
    document = DocumentRecord(id="doc-1", repository_id=repository.id, title="Guide")
    chunk = DocumentChunkRecord(
        id="chunk-1",
        document_id=document.id,
        repository_id=repository.id,
        chunk_index=0,
        text="needle",
        token_count=1,
    )
    with database.session() as session:
        session.add_all([repository, document, chunk])
    retriever = HybridRetriever(
        database=database,
        vector_store=_StubVectorStore(
            {repository.id: [_vector_hit(chunk.id, 0.90)]}
        ),  # type: ignore[arg-type]
        embedding_provider=FakeEmbeddingProvider(EmbeddingSettings(dimension=8)),
    )

    result = await retriever.search("needle", [repository.id])

    assert [hit.chunk_id for hit in result.hits] == [chunk.id]
    assert result.hits[0].keyword_score is None


class _StubVectorStore:
    def __init__(self, hits_by_repository: dict[str, list[VectorHit]]) -> None:
        self.hits_by_repository = hits_by_repository

    def query(self, repository_id: str, embedding: list[float], top_k: int) -> list[VectorHit]:
        del embedding, top_k
        return self.hits_by_repository.get(repository_id, [])


def _vector_hit(chunk_id: str, similarity: float) -> VectorHit:
    return VectorHit(id=chunk_id, text="", metadata={}, similarity=similarity)
