from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.storage.models import DistillationRecord, MemoryChunkRecord, SessionSummaryRecord


@dataclass(frozen=True)
class MemoryHit:
    id: str
    kind: str
    source_id: str
    text: str
    repository_id: str
    similarity: float
    metadata: dict[str, Any]


class MemoryRetriever:
    """Scope-first retrieval over memory collections; SQLite remains authoritative."""

    def __init__(self, database, embedding_provider, vector_store) -> None:
        self.database = database
        self.embedding_provider = embedding_provider
        self.vector_store = vector_store

    async def search(self, query: str, repository_ids: list[str], top_k: int = 5) -> list[MemoryHit]:
        if not query.strip() or not repository_ids or top_k <= 0:
            return []
        embedding = await self.embedding_provider.embed_query(query)
        hits = []
        for repository_id in repository_ids:
            for collection in ("_session_summaries", "_distilled_knowledge"):
                hits.extend(self.vector_store.query_memory(collection, embedding, max(top_k * 3, top_k), repository_id=repository_id))
        hits.sort(key=lambda h: (-h.similarity, h.id))
        vector_ids = {h.id for h in hits}
        with self.database.session() as session:
            owners = {r.vector_id: r for r in session.scalars(select(MemoryChunkRecord).where(MemoryChunkRecord.vector_id.in_(vector_ids)))}
            summaries = {r.id: r.state for r in session.scalars(select(SessionSummaryRecord).where(SessionSummaryRecord.id.in_([o.summary_id for o in owners.values() if o.summary_id])))}
            distillations = {r.id: r.state for r in session.scalars(select(DistillationRecord).where(DistillationRecord.id.in_([o.distillation_id for o in owners.values() if o.distillation_id])))}
        result: list[MemoryHit] = []
        for hit in hits:
            owner = owners.get(hit.id)
            if owner is None or not owner.indexed or owner.repository_id not in repository_ids:
                continue
            source_id = str(hit.metadata.get("source_id", ""))
            kind = str(hit.metadata.get("kind", ""))
            if kind == "session_summary":
                if owner.summary_id != source_id or summaries.get(source_id) != "ready":
                    continue
            elif kind == "distillation":
                if owner.distillation_id != source_id or distillations.get(source_id) not in {"saved", "saved_unindexed"}:
                    continue
            else:
                continue
            result.append(MemoryHit(hit.id, kind, source_id, hit.text, str(hit.metadata.get("repository_id")), hit.similarity, hit.metadata))
        return result[:top_k]
