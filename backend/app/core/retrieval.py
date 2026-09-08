from __future__ import annotations

import re
from dataclasses import dataclass

from rank_bm25 import BM25Okapi
from sqlalchemy import or_, select

from app.core.embedding import EmbeddingProvider
from app.core.multilingual import detect_language, retrieval_threshold
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
    def __init__(
        self,
        *,
        database: Database,
        vector_store: PersistentVectorStore,
        embedding_provider: EmbeddingProvider,
        similarity_threshold: float = 0.65,
    ) -> None:
        self.database = database
        self.vector_store = vector_store
        self.embedding_provider = embedding_provider
        self.similarity_threshold = similarity_threshold
        self.bm25 = BM25Index(database)

    async def search(
        self, query: str, repository_ids: list[str], top_k: int = 5
    ) -> RetrievalResult:
        limit = min(max(top_k, 0), 5)
        if not query.strip() or not repository_ids or limit == 0:
            return RetrievalResult(hits=[], max_score=0.0)
        query_embedding = await self.embedding_provider.embed_query(query)
        vector_hits = sorted(
            [
            hit
            for repository_id in repository_ids
            for hit in self.vector_store.query(repository_id, query_embedding, top_k=10)
            ],
            key=lambda hit: (-hit.similarity, hit.id),
        )[:10]
        keyword_hits = self.bm25.search(query, repository_ids, top_k=10)
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
        threshold = retrieval_threshold(detect_language(query))
        if max_score < threshold:
            return RetrievalResult(hits=[], max_score=max_score)
        vector_scores = {hit.id: hit.similarity for hit in vector_hits}
        keyword_scores = dict(keyword_hits)
        hits: list[RetrievalHit] = []
        for item in fused:
            record = records.get(item.chunk_id)
            if record is None:
                continue
            chunk, document = record
            hits.append(
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
            if len(hits) == limit:
                break
        return RetrievalResult(hits=hits, max_score=max_score)

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
