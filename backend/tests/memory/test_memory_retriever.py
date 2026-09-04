import pytest

from app.memory.retriever import MemoryRetriever
from app.storage.models import MemoryChunkRecord, RepositoryRecord, SessionSummaryRecord
from app.storage.vectorstore import VectorHit


class Embeddings:
    async def embed_query(self, text): return [1.0]


class Vectors:
    def query_memory(self, collection, embedding, top_k, **kwargs):
        return [VectorHit("orphan", "secret", {"repository_id": "repo-a", "source_id": "missing", "kind": "distillation"}, .9)]


class SummaryVectors:
    def __init__(self, source_id): self.source_id = source_id
    def query_memory(self, collection, embedding, top_k, **kwargs):
        if collection != "_session_summaries": return []
        return [VectorHit("summary-vector", "secret", {"repository_id": "repo-a", "source_id": self.source_id, "kind": "session_summary"}, .9)]


@pytest.mark.asyncio
async def test_retriever_drops_vectors_without_sqlite_owner(database):
    assert await MemoryRetriever(database, Embeddings(), Vectors()).search("q", ["repo-a"]) == []


@pytest.mark.asyncio
async def test_retriever_drops_stale_summary_even_with_sqlite_owner(database):
    with database.session() as db:
        from app.storage.models import SessionRecord
        db.add(RepositoryRecord(id="repo-a", name="A"))
        parent = SessionRecord(repository_scope_json='["repo-a"]')
        db.add(parent); db.flush()
        summary = SessionSummaryRecord(session_id=parent.id, state="stale", content="secret", repository_ids_json='["repo-a"]')
        db.add(summary); db.flush()
        db.add(MemoryChunkRecord(summary_id=summary.id, repository_id="repo-a", text="secret", chunk_index=0, vector_id="summary-vector", indexed=True))
        summary_id = summary.id
    assert await MemoryRetriever(database, Embeddings(), SummaryVectors(summary_id)).search("q", ["repo-a"]) == []
