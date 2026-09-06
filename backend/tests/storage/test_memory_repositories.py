
import pytest

from app.api.errors import DomainError
from app.storage.models import DistillationRecord, MessageRecord
from app.storage.repositories import ConversationStore, MemoryStore


def test_empty_scope_summary_is_persisted_but_cannot_own_chunks(database):
    conversations = ConversationStore(database)
    session = conversations.create_session([])
    store = MemoryStore(database)
    summary = store.upsert_summary(session.id, [])
    assert summary.repository_ids_json == "[]"
    with pytest.raises(DomainError, match="scope"):
        store.replace_memory_chunks("session_summary", summary.id, [])


def test_delete_session_detaches_saved_and_deletes_draft(database):
    conversations = ConversationStore(database)
    session = conversations.create_session(["repo-a"])
    conversations.add_message(MessageRecord(session_id=session.id, role="user", content="x"))
    with database.session() as db:
        db.add_all([
            DistillationRecord(session_id=session.id, title="saved", content="x", repository_ids_json='["repo-a"]', state="saved"),
            DistillationRecord(session_id=session.id, title="draft", content="x", repository_ids_json='["repo-a"]', state="draft"),
        ])
    conversations.delete_session(session.id, confirm=True)
    with database.session() as db:
        records = list(db.query(DistillationRecord).all())
        assert len(records) == 1 and records[0].state == "saved" and records[0].session_id is None

