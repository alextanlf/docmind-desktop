"""向量通道：让"零词面重合"的问句也能命中，并且坏了也不影响词法通道。

真实的语义质量由 `scripts/ab_corpus_language_gap.py`（真模型、真语料）度量；这里锁的
是**接线**：查询嵌入找回来的是哪些块、悬空向量怎么被过滤、嵌入坏了会退化成什么。
所以嵌入器是假的、向量是构造的 —— 但走的是真实的 `CorpusVectorIndex` 与 sqlite-vec。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.corpus.vectors import CorpusVectorIndex
from app.schemas.web_search import SearchRequest
from app.search.provider import SearchProviderError
from app.search.web_corpus import WebCorpusSearchProvider
from app.storage.database import Database
from app.storage.models import WebCorpusChunkRecord, WebCorpusPageRecord, new_id
from app.storage.repositories import WebCorpusStore
from app.storage.vectorstore import PersistentVectorStore

DIMENSION = 4

#: 「中文问句」与「英文正文」共用同一个概念向量 —— 模拟多语言嵌入的效果。
CONCEPTS = {
    "asyncio event loop scheduling for concurrent tasks": [1.0, 0.0, 0.0, 0.0],
    "事件循环怎么调度并发任务": [1.0, 0.0, 0.0, 0.0],
    "sqlite write ahead logging for concurrent reads": [0.0, 1.0, 0.0, 0.0],
    "数据库并发读怎么不掉性能": [0.0, 1.0, 0.0, 0.0],
}


class FakeEmbedder:
    """按既定概念返回向量。认不出的文本给一个正交方向，表示"和谁都不像"。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[list[str]] = []

    async def embed_documents(self, texts):
        self.calls.append(list(texts))
        if self.fail:
            raise RuntimeError("embedding model not ready")
        return [CONCEPTS.get(text, [0.0, 0.0, 0.0, 1.0]) for text in texts]

    async def embed_query(self, text):
        if self.fail:
            raise RuntimeError("embedding model not ready")
        return CONCEPTS.get(text, [0.0, 0.0, 0.0, 1.0])


def build(tmp_path: Path):
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'vectors.sqlite3'}")
    database.upgrade()
    store = WebCorpusStore(database)
    vector_store = PersistentVectorStore(_settings(tmp_path))
    index = CorpusVectorIndex(vector_store)
    return store, index


def _settings(tmp_path: Path):
    from app.config import VectorStoreSettings

    return VectorStoreSettings(directory=tmp_path / "vectorstore", dimension=DIMENSION)


def seed(store: WebCorpusStore, *, domain: str = "docs.test") -> list[WebCorpusChunkRecord]:
    page_id = new_id()
    page = WebCorpusPageRecord(
        id=page_id, domain=domain, url="https://docs.test/a", title="A", content_hash="x", byte_size=1
    )
    chunks = [
        WebCorpusChunkRecord(
            page_id=page_id,
            domain=domain,
            url="https://docs.test/a",
            title="A",
            ordinal=0,
            text="asyncio event loop scheduling for concurrent tasks",
        ),
        WebCorpusChunkRecord(
            page_id=page_id,
            domain=domain,
            url="https://docs.test/b",
            title="B",
            ordinal=1,
            text="sqlite write ahead logging for concurrent reads",
        ),
    ]
    store.replace_domain(domain, [page], chunks)
    return chunks


@pytest.mark.asyncio
async def test_index_round_trip_and_delete(tmp_path: Path) -> None:
    _, index = build(tmp_path)
    chunks = seed(build(tmp_path)[0])

    written = index.upsert(chunks, [CONCEPTS[chunk.text] for chunk in chunks])
    assert written == 2

    hits = index.search([1.0, 0.0, 0.0, 0.0], top_k=2)
    assert hits[0].id == chunks[0].id

    index.delete([chunks[0].id])
    assert chunks[0].id not in [hit.id for hit in index.search([1.0, 0.0, 0.0, 0.0], top_k=2)]

    index.clear()
    assert index.search([1.0, 0.0, 0.0, 0.0], top_k=2) == []


@pytest.mark.asyncio
async def test_a_chinese_query_with_no_lexical_overlap_is_still_answered(tmp_path: Path) -> None:
    """**这就是加向量的理由**：纯中文问句对纯英文正文的 token 交集为空，
    词法通道结构上不可能命中（实测 0/10），向量通道能。"""
    store, index = build(tmp_path)
    chunks = seed(store)
    index.upsert(chunks, [CONCEPTS[chunk.text] for chunk in chunks])
    provider = WebCorpusSearchProvider(store, vectors=index, embedder=FakeEmbedder())

    response = await provider.search(SearchRequest(query="事件循环怎么调度并发任务", max_results=3))

    # 排在最前的是语义上真正对应的那一页。第二页（与谁都不像的那个方向）也会出现 ——
    # 向量通道**不设门槛**：实测的余弦分布里相关与不相关几乎完全重叠，任何绝对门槛
    # 要么砍掉真命中、要么放进无关页（见 provider 模块注释里的数字）。
    assert str(response.results[0].canonical_url) == "https://docs.test/a"


@pytest.mark.asyncio
async def test_without_vectors_the_same_query_finds_nothing(tmp_path: Path) -> None:
    """对照组：同一份语料、同一个问句，没有向量通道时零召回。

    这条用例存在的意义是让"向量带来了什么"可被回归验证 —— 否则某天向量悄悄失效，
    中文提问会静默退回到 0 召回，而没有任何测试会红。
    """
    store, _ = build(tmp_path)
    seed(store)
    provider = WebCorpusSearchProvider(store)

    with pytest.raises(SearchProviderError):
        await provider.search(SearchRequest(query="事件循环怎么调度并发任务", max_results=3))


@pytest.mark.asyncio
async def test_embedding_failure_degrades_to_the_lexical_channel(tmp_path: Path) -> None:
    """嵌入模型没就绪不该让本地语料这一级整个失效 —— 词法还能命中的查询照常工作。"""
    store, index = build(tmp_path)
    chunks = seed(store)
    index.upsert(chunks, [CONCEPTS[chunk.text] for chunk in chunks])
    provider = WebCorpusSearchProvider(store, vectors=index, embedder=FakeEmbedder(fail=True))

    # 词面重合的查询仍然命中，而且**完全走词法**（救援规则不介入）。
    response = await provider.search(
        SearchRequest(query="sqlite write ahead logging", max_results=3)
    )
    assert str(response.results[0].canonical_url) == "https://docs.test/b"

    # 零重合的查询则老实报"没有匹配"，交给链上的上游 —— 而不是假装搜到了。
    with pytest.raises(SearchProviderError):
        await provider.search(SearchRequest(query="数据库并发读怎么不掉性能", max_results=3))


@pytest.mark.asyncio
async def test_dangling_vectors_are_filtered_out(tmp_path: Path) -> None:
    """索引里有、语料里没有的向量（删除与写入之间的窗口）不该产出空壳结果。"""
    store, index = build(tmp_path)
    chunks = seed(store)
    index.upsert(chunks, [CONCEPTS[chunk.text] for chunk in chunks])
    store.clear_domain("docs.test")
    provider = WebCorpusSearchProvider(store, vectors=index, embedder=FakeEmbedder())

    with pytest.raises(SearchProviderError):
        await provider.search(SearchRequest(query="事件循环怎么调度并发任务", max_results=3))


@pytest.mark.asyncio
async def test_a_lexical_hit_is_never_overridden_by_a_semantic_one(tmp_path: Path) -> None:
    """救援规则的另一半：词法有命中时向量不介入。

    这对精确查询（标识符、函数名）很重要 —— 语义相近的页不该把字面精确命中的页顶掉。
    """
    store, index = build(tmp_path)
    chunks = seed(store)
    index.upsert(chunks, [CONCEPTS[chunk.text] for chunk in chunks])
    provider = WebCorpusSearchProvider(store, vectors=index, embedder=FakeEmbedder())

    response = await provider.search(SearchRequest(query="asyncio event loop", max_results=3))

    assert str(response.results[0].canonical_url) == "https://docs.test/a"
