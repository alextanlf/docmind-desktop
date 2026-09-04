from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.storage.models import DistillationRecord, SessionSummaryRecord


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
                hits.extend(self.vector_store.query_memory(collection, embedding, top_k, repository_id=repository_id))
        hits.sort(key=lambda h: (-h.similarity, h.id))
        hits = hits[:top_k]
        source_ids = {str(h.metadata.get("source_id")) for h in hits}
        with self.database.session() as session:
            summaries = {r.id: r for r in session.scalars(select(SessionSummaryRecord).where(SessionSummaryRecord.id.in_(source_ids)))}
            distillations = {r.id: r for r in session.scalars(select(DistillationRecord).where(DistillationRecord.id.in_(source_ids)))}
        result: list[MemoryHit] = []
        for hit in hits:
            source_id = str(hit.metadata.get("source_id", ""))
            kind = str(hit.metadata.get("kind", ""))
            record = summaries.get(source_id) if kind == "session_summary" else distillations.get(source_id)
            if record is None:
                continue
            result.append(MemoryHit(hit.id, kind, source_id, hit.text, str(hit.metadata.get("repository_id")), hit.similarity, hit.metadata))
        return result
