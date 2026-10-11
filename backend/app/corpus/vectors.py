"""语料向量：把抓下来的块接进 sqlite-vec，并按块 id 清理。

单独一层的理由和 `crawl.py` 一样是分工：`WebCorpusStore` 不该知道 sqlite-vec 的集合名
与维度校验，检索源也不该知道这些细节。这层只做三件事：写入、删除、查询。

**任何失败都退化成"没有向量"**（返回空、写不进去就记日志），因为向量是增强通道：
它不该因为模型没就绪、维度不匹配、索引文件损坏，就让"本地语料"这一级整个失效。
BM25 永远在，向量只是让中文问句这类零词面重合的情形也能命中。

命名空间用一个伪 repository_id：真实仓库 id 是 UUID，而记忆库走的是另一套集合名，
所以 `web-corpus` 不会与它们相撞（`collection_name` 只做字符替换，不做前缀）。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from app.storage.models import WebCorpusChunkRecord
from app.storage.vectorstore import PersistentVectorStore, VectorHit

logger = logging.getLogger(__name__)

#: 伪 repository_id。选一个**不可能是 UUID** 的名字，避免与真实仓库的集合相撞。
CORPUS_COLLECTION = "web-corpus"

#: 一次写入的批大小。嵌入是本地 ONNX 推理，批太大只是让峰值内存更高，没有收益。
_UPSERT_BATCH = 64


class CorpusVectorIndex:
    def __init__(self, store: PersistentVectorStore) -> None:
        self.store = store

    def upsert(
        self, chunks: Sequence[WebCorpusChunkRecord], embeddings: Sequence[Sequence[float]]
    ) -> int:
        """写入块向量。返回写入条数；失败返回 0（调用方不需要区分失败原因）。"""
        if not chunks or len(chunks) != len(embeddings):
            return 0
        written = 0
        for start in range(0, len(chunks), _UPSERT_BATCH):
            batch = chunks[start : start + _UPSERT_BATCH]
            vectors = embeddings[start : start + _UPSERT_BATCH]
            try:
                self.store.upsert(
                    CORPUS_COLLECTION,
                    ids=[chunk.id for chunk in batch],
                    texts=[chunk.text for chunk in batch],
                    embeddings=[list(vector) for vector in vectors],
                    # domain 也进 metadata：将来要按来源做过滤（"只在这个站里找"）时，
                    # 过滤可以下推到 SQL，而不必先取回再筛。
                    metadatas=[{"domain": chunk.domain, "url": chunk.url} for chunk in batch],
                )
            except Exception as error:  # noqa: BLE001 - 向量写不进去不该让抓取失败
                logger.warning("corpus vector upsert failed: %s", error)
                return written
            written += len(batch)
        return written

    def delete(self, ids: Sequence[str]) -> None:
        if not ids:
            return
        try:
            self.store.delete(CORPUS_COLLECTION, list(ids))
        except Exception as error:  # noqa: BLE001
            # 删不掉只会留下悬空向量（占空间，但检索时会被"块已不存在"过滤掉）。
            logger.warning("corpus vector delete failed: %s", error)

    def clear(self) -> None:
        try:
            self.store.delete_collection(CORPUS_COLLECTION)
        except Exception as error:  # noqa: BLE001
            logger.warning("corpus vector clear failed: %s", error)

    def search(self, embedding: Sequence[float], top_k: int) -> list[VectorHit]:
        """按向量取候选块。没有索引、维度不匹配、索引损坏都返回空列表。"""
        if top_k <= 0 or not embedding:
            return []
        try:
            return self.store.query(CORPUS_COLLECTION, list(embedding), top_k)
        except Exception as error:  # noqa: BLE001 - 增强通道，失败就当没有
            logger.warning("corpus vector search failed: %s", error)
            return []
