from app.storage.models import SessionSummaryRecord
from app.storage.repositories import MemoryStore


def test_restart_marks_generating_summary_retryable(database):
    with database.session() as db:
        from app.storage.models import SessionRecord
        session = SessionRecord(repository_scope_json="[]")
        db.add(session); db.flush()
        db.add(SessionSummaryRecord(session_id=session.id, state="generating"))
    assert MemoryStore(database).recover_interrupted()[0] == 1

