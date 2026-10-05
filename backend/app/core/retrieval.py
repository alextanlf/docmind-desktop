from __future__ import annotations

import re
from dataclasses import dataclass

from rank_bm25 import BM25Okapi
from sqlalchemy import or_, select

from app.core.embedding import EmbeddingProvider
from app.document.chunker import OVERVIEW_SECTION_PATH
from app.schemas.retrieval import RetrievalHit, RetrievalResult
from app.storage.database import Database
from app.storage.models import DocumentChunkRecord, DocumentRecord, EmbeddingRebuildRecord
from app.storage.vectorstore import PersistentVectorStore


def tokenize(text: str) -> list[str]:
    return re.findall(r"[\u4e00-\u9fff]|[A-Za-z_][A-Za-z0-9_]*|\d+", text)


@dataclass(frozen=True)
class FusedChunk:
    chunk_id: str
    score: float


def reciprocal_rank_fusion(vector_ids: list[str], keyword_ids: list[str], k: int = 60) -> list[FusedChunk]:
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    for rank, identifier in enumerate(vector_ids):
        scores[identifier] = scores.get(identifier, 0.0) + 1 / (k + rank + 1)
        first_seen.setdefault(identifier, rank)
    offset = len(vector_ids)
    for rank, identifier in enumerate(keyword_ids):
        scores[identifier] = scores.get(identifier, 0.0) + 1 / (k + rank + 1)
        first_seen.setdefault(identifier, offset + rank)
    return [
        FusedChunk(chunk_id=identifier, score=score)
        for identifier, score in sorted(scores.items(), key=lambda item: (-item[1], first_seen[item[0]]))
    ]


class BM25Index:
    def __init__(self, database: Database) -> None:
        self.database = database

    def search(self, query: str, repository_ids: list[str], top_k: int) -> list[tuple[str, float]]:
        if not repository_ids or top_k <= 0:
            return []
        with self.database.session() as session:
            chunks = list(
                session.scalars(
                    select(DocumentChunkRecord).where(DocumentChunkRecord.repository_id.in_(repository_ids))
                )
            )
        tokens = [tokenize(chunk.text) or ["_"] for chunk in chunks]
        if not chunks or not tokenize(query):
            return []
        scores = BM25Okapi(tokens).get_scores(tokenize(query))
        ranked = sorted(
            zip(chunks, scores, strict=True), key=lambda item: (-float(item[1]), item[0].id)
        )[:top_k]
        return [(chunk.id, float(score)) for chunk, score in ranked if float(score) > 0]


class HybridRetriever:
    """向量 + 词法混合检索，并为命中文档补充「文档概览块」。

    两个与概览块相关的设计约束（均为实测结论，见项目日志 2026-10-01）：

    1. **全局型提问**（「这篇论文提出了什么方法」「主要贡献是什么」）的答案分散在
       标题/摘要里，而相似度检索只会命中「话题最相关」的正文块 —— 实测会把致谢与
       参考文献顶到前面，把标题+摘要挤出 top-k（概览块在这些提问下排第 6~12 名，
       与 top1 只差 0.03~0.08）。所以概览块不能等检索投票，必须**按命中文档强制注入**。
    2. 门控从「绝对分 >= 阈值」改为「有任何命中即可」：固定绝对分对短查询/跨语言查询
       天然不可达（实测跨语言 top1 仅 0.47~0.60，同语言 0.65~0.76），会系统性拦死。
       证据是否充分交给回答用的 LLM 判断 —— `chat/prompts.py` 本就如此要求，
       原来的短路让那段逻辑成了不可达的死代码。
    """

    def __init__(
        self,
        *,
        database: Database,
        vector_store: PersistentVectorStore,
        embedding_provider: EmbeddingProvider,
        include_overview: bool = True,
        overview_documents: int = 3,
        candidate_pool: int = 50,
        max_sources: int = 5,
    ) -> None:
        self.database = database
        self.vector_store = vector_store
        self.embedding_provider = embedding_provider
        self.include_overview = include_overview
        self.overview_documents = max(overview_documents, 0)
        # 送进 RRF 的候选池大小。原来两个通道都硬编码 top_k=10，对单篇文档够用，
        # 但库里 100 篇（约 1600 块）时候选覆盖率只有 0.6%，答案块很容易整个池子都进不来。
        # 召回阶段扩大候选池几乎不花钱（Chroma 一次 ANN 查询 + BM25 本就是全库扫描），
        # 真正的成本在最终送进 LLM 的块数，那仍由 limit 控制。
        self.candidate_pool = max(candidate_pool, 1)
        # 送进 LLM 的块数上限。召回可以放宽，但提示词长度与成本随块数线性增长，
        # 所以这里保留一个显式上限；曾经是 search() 里的裸字面量 5，导致调用方传
        # top_k=20 被无声砍成 5 且没有任何提示。现改为可配置项，默认值不变。
        self.max_sources = max(max_sources, 0)
        self.bm25 = BM25Index(database)

    async def search(
        self, query: str, repository_ids: list[str], top_k: int = 5
    ) -> RetrievalResult:
        limit = min(max(top_k, 0), self.max_sources) if self.max_sources else 0
        if not query.strip() or not repository_ids or limit == 0:
            return RetrievalResult(hits=[], max_score=0.0)
        query_embedding = await self.embedding_provider.embed_query(query)
        pool = self.candidate_pool
        vector_hits = sorted(
            [
            hit
            for repository_id in repository_ids
            for hit in self.vector_store.query(repository_id, query_embedding, top_k=pool)
            ],
            key=lambda hit: (-hit.similarity, hit.id),
        )[:pool]
        keyword_hits = self.bm25.search(query, repository_ids, top_k=pool)
        records = self._records(
            [
                *[hit.id for hit in vector_hits],
                *[identifier for identifier, _ in keyword_hits],
            ],
            repository_ids,
        )
        vector_hits = [hit for hit in vector_hits if hit.id in records]
        keyword_hits = [
            (identifier, score)
            for identifier, score in keyword_hits
            if identifier in records
        ]
        max_score = max((hit.similarity for hit in vector_hits), default=0.0)
        fused = reciprocal_rank_fusion(
            [hit.id for hit in vector_hits], [identifier for identifier, _ in keyword_hits]
        )
        vector_scores = {hit.id: hit.similarity for hit in vector_hits}
        keyword_scores = dict(keyword_hits)
        candidates: list[RetrievalHit] = []
        for item in fused:
            record = records.get(item.chunk_id)
            if record is None:
                continue
            chunk, document = record
            candidates.append(
                RetrievalHit(
                    chunk_id=chunk.id,
                    document_id=document.id,
                    document_title=document.title,
                    text=chunk.text,
                    section_path=chunk.section_path,
                    page_number=chunk.page_number,
                    source_url=chunk.source_url or document.source_url,
                    vector_score=vector_scores.get(chunk.id),
                    keyword_score=keyword_scores.get(chunk.id),
                    fused_score=item.score,
                )
            )
        # 只丢弃「两条通道都没有任何证据」的纯噪声候选（向量分 0 且词法未命中）。
        # 注意这与旧的「绝对分 >= 0.65」完全不同：这里不判断证据是否**充分**
        # （那由回答模型负责），只是拒绝连一点信号都没有的候选。
        hits = [
            hit
            for hit in candidates
            if (hit.vector_score is not None and hit.vector_score > 0.0)
            or (hit.keyword_score is not None and hit.keyword_score > 0.0)
        ][:limit]
        overviews = self._overview_hits(repository_ids, query_embedding) if self.include_overview else []
        return RetrievalResult(hits=[*overviews, *hits], max_score=max_score)

    def _overview_hits(
        self, repository_ids: list[str], query_embedding: list[float]
    ) -> list[RetrievalHit]:
        """为「最相关的若干篇文档」补充概览块。

        概览块是回答全局型提问（「这篇论文提出了什么方法」）的关键输入，但它不能等
        正文检索投票 —— 实测同类干扰文档多起来后，主论文会整篇掉出 top-k
        （33 篇同领域参考文献在场时即复现），此时按「命中文档」取概览就一块都取不到。

        所以这里改为**独立对概览块做一次向量召回**：概览块本身已是短文档，
        在这一层排序比它跟 1600 个正文块一起挤更稳，也不需要新模型或额外 LLM 调用。
        取前 `overview_documents` 篇，仍然受 `include_overview` 开关控制。
        """
        if self.overview_documents == 0:
            return []
        candidates: list[RetrievalHit] = []
        with self.database.session() as session:
            rows = session.execute(
                select(DocumentChunkRecord, DocumentRecord)
                .join(DocumentRecord, DocumentChunkRecord.document_id == DocumentRecord.id)
                .outerjoin(
                    EmbeddingRebuildRecord,
                    EmbeddingRebuildRecord.document_id == DocumentRecord.id,
                )
                .where(
                    DocumentChunkRecord.repository_id.in_(repository_ids),
                    DocumentChunkRecord.section_path == OVERVIEW_SECTION_PATH,
                    or_(
                        EmbeddingRebuildRecord.needs_rebuild.is_(None),
                        EmbeddingRebuildRecord.needs_rebuild.is_(False),
                    ),
                )
            ).all()
        records = {(chunk.id, document.id): (chunk, document) for chunk, document in rows}
        wanted = {
            (hit.id, hit.metadata.get("doc_id"))
            for repository_id in repository_ids
            for hit in self._overview_vector_hits(repository_id, query_embedding)
        }
        candidates = [
            self._overview_hit(records[key], hit)
            for key, hit in ((k, None) for k in wanted)  # 占位，下面按分数填
        ] if False else []
        for repository_id in repository_ids:
            for hit in self._overview_vector_hits(repository_id, query_embedding):
                record = records.get((hit.id, hit.metadata.get("doc_id")))
                if record is None:
                    continue
                candidates.append(self._overview_hit(record, hit))
        unique: dict[str, RetrievalHit] = {}
        for hit in sorted(candidates, key=lambda item: -(item.vector_score or 0.0)):
            unique.setdefault(hit.chunk_id, hit)
        return list(unique.values())[: self.overview_documents]

    def _overview_vector_hits(self, repository_id: str, query_embedding: list[float]) -> list:
        """按 section_path 过滤出概览块的向量召回。"""
        try:
            return self.vector_store.query(
                repository_id, query_embedding, top_k=self.overview_documents,
                where={"section_path": OVERVIEW_SECTION_PATH},
            )
        except TypeError:
            # 兼容不接受 where 的存储实现（含测试替身）：多取一些再本地过滤。
            hits = self.vector_store.query(
                repository_id, query_embedding, top_k=max(self.overview_documents * 4, 20)
            )
            return [
                hit for hit in hits if hit.metadata.get("section_path") == OVERVIEW_SECTION_PATH
            ][: self.overview_documents]

    @staticmethod
    def _overview_hit(
        record: tuple[DocumentChunkRecord, DocumentRecord], vector_hit
    ) -> RetrievalHit:
        chunk, document = record
        return RetrievalHit(
            chunk_id=chunk.id,
            document_id=document.id,
            document_title=document.title,
            text=chunk.text,
            section_path=chunk.section_path,
            page_number=chunk.page_number,
            source_url=chunk.source_url or document.source_url,
            vector_score=vector_hit.similarity,
            fused_score=0.0,
        )

    def _records(
        self, chunk_ids: list[str], repository_ids: list[str]
    ) -> dict[str, tuple[DocumentChunkRecord, DocumentRecord]]:
        if not chunk_ids:
            return {}
        with self.database.session() as session:
            rows = session.execute(
                select(DocumentChunkRecord, DocumentRecord)
                .join(DocumentRecord, DocumentChunkRecord.document_id == DocumentRecord.id)
                .outerjoin(
                    EmbeddingRebuildRecord,
                    EmbeddingRebuildRecord.document_id == DocumentRecord.id,
                )
                .where(
                    DocumentChunkRecord.id.in_(chunk_ids),
                    DocumentChunkRecord.repository_id.in_(repository_ids),
                    or_(
                        EmbeddingRebuildRecord.needs_rebuild.is_(None),
                        EmbeddingRebuildRecord.needs_rebuild.is_(False),
                    ),
                )
            ).all()
        return {chunk.id: (chunk, document) for chunk, document in rows}
