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
