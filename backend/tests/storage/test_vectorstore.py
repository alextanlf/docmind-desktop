from __future__ import annotations

import tempfile
from pathlib import Path

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


def test_vector_store_maps_read_failures_to_index_failed(tmp_path) -> None:
    store = PersistentVectorStore(VectorStoreSettings(directory=tmp_path / "vectors"))
    # 先建集合并写入：集合不存在时 query 直接返回空，不会走到读取路径，
    # 注入点也就不会被触发（这个坑踩过一次）。
    store.upsert("repo-1", ["chunk-1"], ["text"], [[1.0, 0.0]], [{"doc_id": "doc-1"}])

    def fail_dimension(collection: str):
        del collection
        raise RuntimeError("database unavailable")

    # 注入点在真正的读取路径上，而不是某个后端特有的 client 对象 ——
    # 后者会把测试焊死在实现细节上（上一版就因此直接失效）。
    store._stored_dimension = fail_dimension  # type: ignore[method-assign]

    with pytest.raises(DomainError) as error:
        store.query("repo-1", [1.0, 0.0], top_k=1)

    assert error.value.code == "INDEX_FAILED"


def test_vector_store_where_filter_does_not_lose_sparse_matches(tmp_path) -> None:
    """过滤条件命中项稀少时，必须仍能全部取回 —— 漏召回是静默故障。

    曾经的实现是「按相似度取 top_k×10 条再在 Python 里筛」，于是低选择性
    过滤时匹配项一旦排在窗口之外就取不到：实测 100 条里 2 条匹配
    doc_id='d1'、top_k=3（窗口 30）时返回 **0 条**，且无任何报错。
    概览块注入（retrieval.py:235 按 section_path 取 OVERVIEW）会因此悄悄失效。

    现在过滤下推到 SQL，由扩展在 ANN 遍历时筛选，语义与选择率无关。
    """
    store = PersistentVectorStore(VectorStoreSettings(directory=tmp_path / "vectors"))
    count = 100
    store.upsert(
        "repo-sparse",
        [f"chunk-{index}" for index in range(count)],
        [f"text-{index}" for index in range(count)],
        [[1.0, 0.0] for _ in range(count)],
        # 只有前 2 条匹配；放大窗口按相似度取时它们会落在窗口外
        [{"doc_id": "d1" if index < 2 else "d2"} for index in range(count)],
    )

    hits = store.query("repo-sparse", [1.0, 0.0], top_k=3, where={"doc_id": "d1"})

    assert {hit.id for hit in hits} == {"chunk-0", "chunk-1"}


def test_vector_store_where_filter_finds_match_at_the_bottom_ranking(tmp_path) -> None:
    """唯一命中的条目即使相似度最低，也必须被取回。

    这是上一条 bug 的极端形态：把匹配项放在「最不相似」的位置，
    任何「先按相似度截断再筛」的策略都会漏掉它。
    """
    store = PersistentVectorStore(VectorStoreSettings(directory=tmp_path / "vectors"))
    store.upsert(
        "repo-bottom",
        ["wanted"] + [f"other-{index}" for index in range(99)],
        ["text"] * 100,
        [[0.0, 1.0]] + [[1.0, 0.0] for _ in range(99)],
        [{"source_type": "WANTED"}] + [{"source_type": "OTHER"} for _ in range(99)],
    )

    hits = store.query("repo-bottom", [1.0, 0.0], top_k=3, where={"source_type": "WANTED"})

    assert [hit.id for hit in hits] == ["wanted"]


def test_vector_store_where_filter_handles_int_bool_and_zero_values(tmp_path) -> None:
    """过滤列的值统一字符串化，类型语义必须保持一致。

    三个易错点：int 与 str 在 SQLite 里都存成 text（`'3'` 不应匹配 `3`）、
    bool 走 `str(True)=='True'` 而 JSON 是 `true`（两边规则必须一样）、
    `0` 是 falsy 但完全有效（不能用 `if value` 判空）。
    """
    store = PersistentVectorStore(VectorStoreSettings(directory=tmp_path / "vectors"))
    store.upsert(
        "repo-types",
        ["int-hit", "int-miss", "bool-hit", "bool-miss", "zero-hit"],
        ["t"] * 5,
        [[1.0, 0.0]] * 5,
        [
            {"page_number": 3},
            {"page_number": 4},
            {"chunk_index": True},
            {"chunk_index": False},
            {"source_type": 0},
        ],
    )

    assert [hit.id for hit in store.query("repo-types", [1.0, 0.0], 5, where={"page_number": 3})] == [
        "int-hit"
    ]
    assert [hit.id for hit in store.query("repo-types", [1.0, 0.0], 5, where={"chunk_index": True})] == [
        "bool-hit"
    ]
    assert [hit.id for hit in store.query("repo-types", [1.0, 0.0], 5, where={"source_type": 0})] == [
        "zero-hit"
    ]
    # 类型不符不应匹配 —— 存成 text 的 '3' 不能匹配查询值 3
    assert store.query("repo-types", [1.0, 0.0], 5, where={"page_number": "3"}) == []


def test_vector_store_concurrent_writes_and_reads(tmp_path) -> None:
    """多线程并发读写不得报错，且各线程的集合互不干扰。

    检索在事件循环、索引写入在 worker 线程，sqlite3 连接不能跨线程共享，
    实现用一把 RLock 串行化 —— 这个用例锁住该假设。
    """
    import threading

    store = PersistentVectorStore(VectorStoreSettings(directory=tmp_path / "vectors"))
    store.upsert("repo-shared", ["seed"], ["t"], [[1.0, 0.0]], [{"doc_id": "d"}])
    errors: list[str] = []
    guard = threading.Lock()

    def writer(index: int) -> None:
        try:
            for step in range(15):
                store.upsert(
                    f"repo-w{index}",
                    [f"w{index}-{step}"],
                    ["t"],
                    [[1.0, 0.0]],
                    [{"doc_id": f"d{index}"}],
                )
        except Exception as error:  # noqa: BLE001 - 测试要看到任何失败
            with guard:
                errors.append(repr(error))

    def reader() -> None:
        try:
            for _ in range(15):
                store.query("repo-shared", [1.0, 0.0], 3)
        except Exception as error:  # noqa: BLE001
            with guard:
                errors.append(repr(error))

    threads = [threading.Thread(target=writer, args=(index,)) for index in range(6)]
    threads += [threading.Thread(target=reader) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(store.query("repo-w0", [1.0, 0.0], 50)) == 15
    assert len(store.query("repo-w5", [1.0, 0.0], 50)) == 15
    assert [hit.id for hit in store.query("repo-shared", [1.0, 0.0], 3)] == ["seed"]


def test_vector_store_accepts_uuid_repository_and_chunk_ids() -> None:
    """repository_id 与 chunk id 在生产里都是 UUID。

    集合名因此含连字符（repo_<uuid>），未加引号时 SQLite 会把它当减法解析并报
    `near "-": syntax error`。这个用例锁住标识符加引号的行为。
    """
    import uuid

    settings = VectorStoreSettings(directory=Path(tempfile.mkdtemp()) / "vectors")
    store = PersistentVectorStore(settings)
    repository_id = str(uuid.uuid4())
    chunk_ids = [str(uuid.uuid4()) for _ in range(2)]
    embeddings = [[1.0, 0.0], [0.0, 1.0]]

    store.upsert(
        repository_id,
        chunk_ids,
        ["Virtual Thread", "Structured Concurrency"],
        embeddings,
        [{"doc_id": chunk_ids[0]}, {"doc_id": chunk_ids[1]}],
    )

    hits = store.query(repository_id, embeddings[0], top_k=2)
    assert {hit.id for hit in hits} == set(chunk_ids)
    assert {hit.id for hit in store.query(repository_id, embeddings[0], 2, where={"doc_id": chunk_ids[0]})} == {
        chunk_ids[0]
    }

    store.delete_collection(repository_id)
    assert store.query(repository_id, embeddings[0], top_k=2) == []


def test_vector_store_rejects_dimension_mismatch_on_query(tmp_path) -> None:
    """换嵌入模型后维度会变，必须给出「需要重建索引」而不是裸 sqlite 错误。"""
    store = PersistentVectorStore(VectorStoreSettings(directory=tmp_path / "vectors"))
    store.upsert("repo-1", ["chunk-1"], ["text"], [[1.0, 0.0]], [{"doc_id": "doc-1"}])

    with pytest.raises(DomainError, match="重建索引") as error:
        store.query("repo-1", [1.0, 0.0, 0.0, 0.0], top_k=1)

    assert error.value.code == "INDEX_FAILED"


def test_vector_store_handles_more_ids_than_sqlite_variable_limit(tmp_path) -> None:
    """批量条数超过 SQLite 绑定变量上限（32766）时仍要能写入与删除。

    `WHERE id IN (?,?,...)` 会按条数展开绑定变量，一次性传 5 万个会报
    `too many SQL variables`。这里用超过上限的条数锁住分批逻辑。
    条数取 33000：既越过 32766，又让用例跑在可接受时间内。
    """
    store = PersistentVectorStore(VectorStoreSettings(directory=tmp_path / "vectors"))
    count = 33_000
    ids = [f"chunk-{index}" for index in range(count)]
    # 2 维足够验证「与维度无关」的批量行为，避免测试跑太久。
    embeddings = [[1.0, 0.0] if index == 0 else [0.0, 1.0] for index in range(count)]

    store.upsert(
        "repo-bulk",
        ids,
        [f"text-{index}" for index in range(count)],
        embeddings,
        [{"doc_id": "doc-1"} for _ in range(count)],
    )
    assert len(store.list_stored("repo-bulk")) == count
    assert store.query("repo-bulk", embeddings[0], top_k=3)[0].id == "chunk-0"

    store.delete("repo-bulk", ids)
    assert store.list_stored("repo-bulk") == []
