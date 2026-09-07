
from sqlalchemy.exc import IntegrityError

from app.storage.database import Database
from app.storage.models import OllamaPullRecord
from app.storage.repositories import OllamaPullStore


def test_pull_store_recovers_queued_and_running(tmp_path):
    db = Database(f"sqlite+pysqlite:///{tmp_path/'db.sqlite'}"); db.upgrade()
    store = OllamaPullStore(db)
    store.save(OllamaPullRecord(id="a", model_name="m", base_url="http://127.0.0.1:11434", state="running"))
    store.recover_interrupted()
    row = store.get("a")
    assert row.state == "failed" and row.error_code == "OLLAMA_PULL_INTERRUPTED"


def test_pull_store_create_rejects_second_active_pull_at_same_address(database):
    store = OllamaPullStore(database)
    first = store.create(model_name="qwen2.5:7b", base_url="http://127.0.0.1:11434")
    assert store.find_active("http://127.0.0.1:11434").id == first.id
    try:
        store.create(model_name="llama3.2", base_url="http://127.0.0.1:11434")
    except IntegrityError:
        pass
    else:
        raise AssertionError("active address uniqueness was not enforced")


def test_pull_store_update_and_event_sequence_are_persisted(database):
    store = OllamaPullStore(database)
    record = store.create(model_name="qwen2.5:7b", base_url="http://127.0.0.1:11434")
    updated = store.update(record.id, state="running", progress=37, status="downloading")
    assert updated.state == "running" and updated.progress == 37
    assert store.allocate_event_sequence(record.id) == 1
    assert store.allocate_event_sequence(record.id) == 2
    assert store.get(record.id).last_event_sequence == 2


def test_pull_store_recover_on_startup_marks_terminal_metadata(database):
    store = OllamaPullStore(database)
    queued = store.create(model_name="qwen2.5:7b", base_url="http://127.0.0.1:11434")
    store.update(queued.id, state="queued")
    store.recover_on_startup()
    recovered = store.get(queued.id)
    assert recovered.state == "failed"
    assert recovered.error_code == "OLLAMA_PULL_INTERRUPTED"
    assert recovered.completed_at is not None
    store.recover_on_startup()
    assert store.get(queued.id).error_code == "OLLAMA_PULL_INTERRUPTED"
