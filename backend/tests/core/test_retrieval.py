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
async def low_retriever(database, tmp_path) -> HybridRetriever:
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
    provider = FakeEmbeddingProvider(EmbeddingSettings(dimension=8))
    with database.session() as session:
        session.add_all([repository, document, chunk])
    store = PersistentVectorStore(VectorStoreSettings(directory=tmp_path / "vectors"))
    # 存正文自身的向量，让「共享部分词」的提问拿到低但为正的相似度，
    # 而完全无关的提问拿到 0 —— 两者分别对应保留与丢弃两种行为。
    store.upsert(
        repository.id,
        [chunk.id],
        [chunk.text],
        [await provider.embed_query(chunk.text)],
        [{"doc_id": document.id, "doc_title": document.title, "section_path": "State"}],
    )
    return HybridRetriever(
        database=database,
        vector_store=store,
        embedding_provider=provider,
        similarity_threshold=0.65,
    )


async def test_hybrid_search_keeps_low_score_hits_for_the_model_to_judge(
    low_retriever: HybridRetriever,
) -> None:
    """低分不再被前置门控丢弃，而是连同真实分数交给回答模型判断证据是否充分。

    原实现用 `retrieval_threshold(语种)` 的固定绝对分（zh 0.65 / en 0.55）提前返回空结果，
    实测跨语言 top1 仅 0.47~0.60、短查询同语言也只有 0.58，会系统性拦死中文提问检索英文文档；
    同时 `chat/service.py` 的 `if not sources:` 使 `prompts.py` 的「（无可用文档片段）」
    分支成为不可达死代码。
    """
    result = await low_retriever.search("State", ["repo-1"])

    assert [hit.chunk_id for hit in result.hits] == ["chunk-1"]
    assert 0.0 < result.max_score < 0.65
    assert result.hits[0].vector_score == result.max_score


async def test_hybrid_search_drops_candidates_with_no_evidence_at_all(
    low_retriever: HybridRetriever,
) -> None:
    """两条通道都没有任何信号时不返回候选。

    这与旧的「绝对分 >= 0.65」不同：这里不判断证据是否**充分**（由回答模型负责），
    只拒绝连一点信号都没有的纯噪声候选（向量分 0 且词法未命中）。
    """
    result = await low_retriever.search("不存在的 API", ["repo-1"])

    assert result.hits == []
    assert result.max_score == 0.0


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

    assert [hit.chunk_id for hit in result.hits] == [chunk.id]
    assert result.max_score == 0.40


async def test_overview_chunk_is_injected_by_its_own_vector_recall(database, tmp_path) -> None:
    """概览块靠**独立的一次向量召回**注入，不依赖它是否被正文检索命中。

    回归点：同类干扰文档多起来后，主论文会整篇掉出正文 top-k（33 篇同领域参考文献
    在场即复现），此时若按「命中文档」取概览就一块都取不到，全局型提问随之失败。
    """
    repository = RepositoryRecord(id="repo-1", name="Repository")
    document = DocumentRecord(id="doc-1", repository_id=repository.id, title="Radar-APLANC Paper")
    overview = DocumentChunkRecord(
        id="chunk-overview",
        document_id=document.id,
        repository_id=repository.id,
        chunk_index=-1,
        text="标题：Radar-APLANC Paper\nAbstract We propose Radar-APLANC.",
        token_count=12,
        section_path="文档概览",
    )
    body = DocumentChunkRecord(
        id="chunk-body",
        document_id=document.id,
        repository_id=repository.id,
        chunk_index=0,
        text="We propose Radar-APLANC, the first unsupervised framework.",
        token_count=8,
        section_path="Introduction",
    )
    with database.session() as session:
        session.add_all([repository, document, overview, body])
    provider = FakeEmbeddingProvider(EmbeddingSettings(dimension=8))
    store = PersistentVectorStore(VectorStoreSettings(directory=tmp_path / "vectors"))
    store.upsert(
        repository.id,
        [body.id, overview.id],
        [body.text, overview.text],
        [await provider.embed_query(body.text), await provider.embed_query(overview.text)],
        [
            {"doc_id": document.id, "doc_title": document.title, "section_path": "Introduction"},
            {"doc_id": document.id, "doc_title": document.title, "section_path": "文档概览"},
        ],
    )
    result = await HybridRetriever(
        database=database, vector_store=store, embedding_provider=provider, similarity_threshold=0.65
    ).search("这篇论文提出了什么方法", [repository.id])

    # 概览块排在最前，且它是按自己的向量召回进来的（正文召回里根本没有它）
    assert result.hits[0].chunk_id == "chunk-overview"
    assert result.hits[0].section_path == "文档概览"
    assert result.hits[0].vector_score is not None
    assert "chunk-body" in [hit.chunk_id for hit in result.hits]


async def test_overview_injection_is_bounded_and_can_be_disabled(database) -> None:
    repository = RepositoryRecord(id="repo-1", name="Repository")
    documents = [
        DocumentRecord(id=f"doc-{index}", repository_id=repository.id, title=f"标题 {index}")
        for index in range(4)
    ]
    chunks: list[DocumentChunkRecord] = []
    for index, document in enumerate(documents):
        chunks.append(
            DocumentChunkRecord(
                id=f"overview-{index}",
                document_id=document.id,
                repository_id=repository.id,
                chunk_index=-1,
                text=f"文档 {index} 概览",
                token_count=4,
                section_path="文档概览",
            )
        )
        chunks.append(
            DocumentChunkRecord(
                id=f"body-{index}",
                document_id=document.id,
                repository_id=repository.id,
                chunk_index=0,
                text=f"文档 {index} 正文",
                token_count=4,
                section_path="正文",
            )
        )
    with database.session() as session:
        session.add_all([repository, *documents, *chunks])

    def overview_hit(index: int) -> VectorHit:
        return VectorHit(
            id=f"overview-{index}",
            text=f"文档 {index} 概览",
            similarity=0.5 - index * 0.01,
            metadata={"section_path": "文档概览", "doc_id": f"doc-{index}"},
        )

    store = _StubVectorStore(
        {repository.id: [_vector_hit(f"body-{index}", 0.9) for index in range(4)]},
        extra={repository.id: [overview_hit(index) for index in range(4)]},
    )

    bounded = await HybridRetriever(
        database=database,
        vector_store=store,  # type: ignore[arg-type]
        embedding_provider=FakeEmbeddingProvider(EmbeddingSettings(dimension=8)),
        overview_documents=2,
    ).search("问题", [repository.id])
    disabled = await HybridRetriever(
        database=database,
        vector_store=store,  # type: ignore[arg-type]
        embedding_provider=FakeEmbeddingProvider(EmbeddingSettings(dimension=8)),
        include_overview=False,
    ).search("问题", [repository.id])

    assert [hit.chunk_id for hit in bounded.hits] == [
        "overview-0",
        "overview-1",
        "body-0",
        "body-1",
        "body-2",
        "body-3",
    ]
    # 关闭后完全不注入
    assert [hit.chunk_id for hit in disabled.hits] == [f"body-{index}" for index in range(4)]
    assert not [hit for hit in disabled.hits if hit.section_path == "文档概览"]


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
    """测试替身。`extra` 模拟「带 where 过滤的概览块召回」用的第二组结果。"""

    def __init__(
        self,
        hits_by_repository: dict[str, list[VectorHit]],
        extra: dict[str, list[VectorHit]] | None = None,
    ) -> None:
        self.hits_by_repository = hits_by_repository
        self.extra = extra or {}

    def query(self, repository_id: str, embedding: list[float], top_k: int, **kwargs) -> list[VectorHit]:
        del embedding, top_k
        where = kwargs.get("where")
        if where and where.get("section_path") == "文档概览":
            return self.extra.get(repository_id, [])
        return self.hits_by_repository.get(repository_id, [])


def _vector_hit(chunk_id: str, similarity: float) -> VectorHit:
    return VectorHit(id=chunk_id, text="", metadata={}, similarity=similarity)


async def test_candidate_pool_widens_what_is_sent_to_rrf(database) -> None:
    """候选池要把两个通道的召回量送到 RRF，否则库里文档一多答案块根本进不了池子。

    回归点：原来向量与 BM25 两个通道都硬编码 top_k=10，100 篇（约 1600 块）时候选覆盖率仅 0.6%。
    """
    repository = RepositoryRecord(id="repo-1", name="Repository")
    document = DocumentRecord(id="doc-1", repository_id=repository.id, title="Guide")
    with database.session() as session:
        session.add_all([repository, document])
        session.add_all(
            [
                DocumentChunkRecord(
                    id=f"chunk-{index}",
                    document_id=document.id,
                    repository_id=repository.id,
                    chunk_index=index,
                    text=f"needle {index}",
                    token_count=2,
                )
                for index in range(40)
            ]
        )
    store = _StubVectorStore(
        {"repo-1": [_vector_hit(f"chunk-{index}", 0.9 - index * 0.001) for index in range(40)]}
    )
    retriever = HybridRetriever(
        database=database,
        vector_store=store,  # type: ignore[arg-type]
        embedding_provider=FakeEmbeddingProvider(EmbeddingSettings(dimension=8)),
        candidate_pool=50,
    )

    result = await retriever.search("needle", [repository.id])

    assert retriever.candidate_pool == 50
    assert len(result.hits) == 5  # 最终送进 prompt 的仍是 limit，池子只影响排序质量
    assert result.hits[0].vector_score > 0.8


async def test_hybrid_search_max_sources_is_configurable(database) -> None:
    """The 5-chunk cap was a bare literal inside search(), so callers passing
    top_k=20 were silently trimmed. It is now an explicit constructor knob whose
    default keeps the previous behaviour."""
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
        for index in range(8)
    ]
    with database.session() as session:
        session.add_all([repository, document, *chunks])

    def build(max_sources: int) -> HybridRetriever:
        return HybridRetriever(
            database=database,
            vector_store=_StubVectorStore(
                {"repo-1": [_vector_hit(chunk.id, 0.9) for chunk in chunks]}
            ),
            embedding_provider=FakeEmbeddingProvider(EmbeddingSettings(dimension=8)),
            max_sources=max_sources,
        )

    # Default cap still applies and still trims silently-but-consistently at 5.
    assert len((await build(5).search("needle", [repository.id], top_k=20)).hits) == 5
    # Raising the knob actually raises the ceiling.
    assert len((await build(8).search("needle", [repository.id], top_k=20)).hits) == 8
    # A raised ceiling still yields at most top_k.
    assert len((await build(8).search("needle", [repository.id], top_k=2)).hits) == 2
