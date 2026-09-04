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

    def __init__(self, database, embedding_provider, vector_store, *, similarity_threshold: float = 0.65) -> None:
        self.database = database
        self.embedding_provider = embedding_provider
        self.vector_store = vector_store
        self.similarity_threshold = similarity_threshold

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
            summaries = {r.id: r for r in session.scalars(select(SessionSummaryRecord).where(SessionSummaryRecord.id.in_([o.summary_id for o in owners.values() if o.summary_id])))}
            distillations = {r.id: r for r in session.scalars(select(DistillationRecord).where(DistillationRecord.id.in_([o.distillation_id for o in owners.values() if o.distillation_id])))}
        result: list[MemoryHit] = []
        seen: set[tuple[str, str]] = set()
        for hit in hits:
            owner = owners.get(hit.id)
            if owner is None or not owner.indexed or owner.repository_id not in repository_ids or hit.similarity < self.similarity_threshold:
                continue
            source_id = str(hit.metadata.get("source_id", ""))
            kind = str(hit.metadata.get("kind", ""))
            if kind == "session_summary":
                source = summaries.get(source_id)
                if owner.summary_id != source_id or source is None or source.state != "ready":
                    continue
                session_id = source.session_id
            elif kind == "distillation":
                source = distillations.get(source_id)
                if owner.distillation_id != source_id or source is None or source.state != "saved":
                    continue
                session_id = source.session_id
            else:
                continue
            key = (kind, source_id)
            if key in seen:
                continue
            seen.add(key)
            metadata = {"kind": kind, "source_id": source_id, "repository_id": owner.repository_id, "session_id": session_id}
            result.append(MemoryHit(hit.id, kind, source_id, owner.text, owner.repository_id, hit.similarity, metadata))
        return result[:top_k]
