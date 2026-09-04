def test_memory_cleanup_replay_is_wired(client):
    assert hasattr(client.app.state, "memory_store")


def test_pending_memory_owner_is_not_retrievable(database):
    from app.storage.models import (
        MemoryChunkRecord,
        RepositoryRecord,
        SessionRecord,
        SessionSummaryRecord,
    )
    from app.storage.repositories import MemoryStore

    with database.session() as db:
        db.add(RepositoryRecord(id="repo-a", name="A"))
        session = SessionRecord(repository_scope_json='["repo-a"]')
        db.add(session); db.flush()
        summary = SessionSummaryRecord(session_id=session.id, state="ready", content="new", repository_ids_json='["repo-a"]')
        db.add(summary); db.flush()
        db.add(MemoryChunkRecord(summary_id=summary.id, repository_id="repo-a", text="new", chunk_index=0, vector_id="v", indexed=False))
    assert MemoryStore(database).pending_memory_sources()[0] == {summary.id}
