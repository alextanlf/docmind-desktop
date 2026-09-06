import pytest

from app.memory.retriever import MemoryRetriever
from app.storage.models import (
    DistillationRecord,
    MemoryChunkRecord,
    RepositoryRecord,
    SessionSummaryRecord,
)
from app.storage.vectorstore import VectorHit


class Embeddings:
    async def embed_query(self, text): return [1.0]


class Vectors:
    def query_memory(self, collection, embedding, top_k, **kwargs):
        return [VectorHit("orphan", "secret", {"repository_id": "repo-a", "source_id": "missing", "kind": "distillation"}, .9)]


class SummaryVectors:
    def __init__(self, source_id): self.source_id = source_id
    def query_memory(self, collection, embedding, top_k, **kwargs):
        if collection not in {"_session_summaries", "session_summaries"}: return []
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


@pytest.mark.asyncio
async def test_retriever_filters_low_similarity_and_deduplicates_authoritative_source(database):
    with database.session() as db:
        db.add_all([RepositoryRecord(id="repo-a", name="A"), RepositoryRecord(id="repo-b", name="B")])
        record = DistillationRecord(title="D", content="trusted", state="saved", repository_ids_json='["repo-a","repo-b"]')
        db.add(record); db.flush()
        db.add_all([
            MemoryChunkRecord(distillation_id=record.id, repository_id="repo-a", text="trusted", chunk_index=0, vector_id="v-a", indexed=True),
            MemoryChunkRecord(distillation_id=record.id, repository_id="repo-b", text="trusted", chunk_index=0, vector_id="v-b", indexed=True),
        ])
        source_id = record.id

    class DuplicateVectors:
        def query_memory(self, collection, embedding, top_k, **kwargs):
            if collection not in {"_distilled_knowledge", "distilled_knowledge"}: return []
            repository_id = kwargs["repository_id"]
            score = .9 if repository_id == "repo-a" else .2
            return [VectorHit(f"v-{repository_id[-1]}", "stale metadata", {"repository_id": repository_id, "source_id": source_id, "kind": "distillation", "session_id": "deleted"}, score)]

    hits = await MemoryRetriever(database, Embeddings(), DuplicateVectors()).search("q", ["repo-a", "repo-b"])
    assert len(hits) == 1
    assert hits[0].text == "trusted"
    assert hits[0].metadata["session_id"] is None


@pytest.mark.asyncio
async def test_retriever_drops_saved_unindexed_distillation(database):
    with database.session() as db:
        db.add(RepositoryRecord(id="repo-a", name="A"))
        record = DistillationRecord(title="D", content="pending", state="saved_unindexed", repository_ids_json='["repo-a"]')
        db.add(record); db.flush()
        db.add(MemoryChunkRecord(distillation_id=record.id, repository_id="repo-a", text="pending", chunk_index=0, vector_id="pending-vector", indexed=True))
        source_id = record.id
    class PendingVectors:
        def query_memory(self, collection, embedding, top_k, **kwargs):
            return [VectorHit("pending-vector", "pending", {"repository_id": "repo-a", "source_id": source_id, "kind": "distillation"}, .9)] if collection in {"_distilled_knowledge", "distilled_knowledge"} else []
    assert await MemoryRetriever(database, Embeddings(), PendingVectors()).search("q", ["repo-a"]) == []
