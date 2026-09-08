from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from app.storage.database import Database
from app.storage.repositories import RepositorySyncStateStore


def test_phase4b1_migration_adds_sync_state_and_remote_deleted(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'db.sqlite'}")
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).parents[2] / "migrations"))
    config.set_main_option("sqlalchemy.url", database.url)
    with database.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0011_phase4b1_sync_state")

    tables = set(inspect(database.engine).get_table_names())
    assert {"repository_sync_state", "repository_sync_meta"} <= tables
    document_columns = {
        column["name"] for column in inspect(database.engine).get_columns("documents")
    }
    assert "remote_deleted" in document_columns


def test_sync_state_upsert_and_snapshot(database: Database) -> None:
    store = RepositorySyncStateStore(database)
    store.upsert("repo-1", "doc-1", "State", "abc123", "https://yuque.com/repo-1/doc-1")

    snapshot = store.get_snapshot("repo-1")
    assert snapshot["doc-1"].title == "State"
    assert snapshot["doc-1"].content_sha256 == "abc123"

    store.upsert("repo-1", "doc-1", "State 2", "def456", "https://yuque.com/repo-1/doc-1")
    assert store.get_snapshot("repo-1")["doc-1"].content_sha256 == "def456"
    assert store.get_snapshot("repo-2") == {}


def test_sync_last_synced_at_roundtrip(database: Database) -> None:
    store = RepositorySyncStateStore(database)
    assert store.last_synced_at("repo-1") is None

    store.set_last_synced_at("repo-1", "2026-09-08T00:00:00+00:00")
    assert store.last_synced_at("repo-1") == "2026-09-08T00:00:00+00:00"
