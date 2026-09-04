
import pytest

from app.memory.indexer import MemoryIndexer
from app.storage.models import MemoryChunkRecord, RepositoryRecord
from app.storage.repositories import ConversationStore, MemoryStore


class Embeddings:
    async def embed_documents(self, texts): return [[1.0] for _ in texts]


class Vectors:
    def __init__(self): self.items = []
    def upsert_memory(self, collection, ids, embeddings, texts, metadatas): self.items.extend(zip(ids, texts, metadatas))
    def delete_memory(self, collection, ids): pass


@pytest.mark.asyncio
async def test_indexer_persists_sqlite_ownership_and_exact_vector_ids(database):
    with database.session() as db:
        db.add_all([RepositoryRecord(id="repo-a", name="A"), RepositoryRecord(id="repo-b", name="B")])
    session = ConversationStore(database).create_session(["repo-a", "repo-b"])
    summary = MemoryStore(database).upsert_summary(session.id, ["repo-a", "repo-b"])
    MemoryStore(database).complete_summary(summary.id, content="remember", topics=[], now=session.created_at)
    vectors = Vectors()
    assert await MemoryIndexer(database, Embeddings(), vectors).index_summary(summary.id) == 2
    with database.session() as db:
        chunks = list(db.query(MemoryChunkRecord).all())
        assert {c.vector_id for c in chunks} == {
            f"memory:session_summary:{summary.id}:repo-a:0",
            f"memory:session_summary:{summary.id}:repo-b:0",
        }
