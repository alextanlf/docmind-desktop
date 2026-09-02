from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from app.storage.database import Database


def upgrade_from_revision(tmp_path: Path, revision: str) -> Database:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'phase2.sqlite3'}")
    backend_dir = Path(__file__).resolve().parents[2]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database.url)
    with database.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)
    return database


def test_phase2_migration_adds_batch_tables_and_source_identity(tmp_path: Path) -> None:
    database = upgrade_from_revision(tmp_path, "0004_chat_request_idempotency")
    try:
        database.upgrade()
        inspector = inspect(database.engine)

        assert {"source_identity", "source_revision"}.issubset(
            {column["name"] for column in inspector.get_columns("documents")}
        )
        assert {"batch_imports", "batch_items"}.issubset(inspector.get_table_names())
        assert "uq_documents_repository_source_identity" in {
            index["name"] for index in inspector.get_indexes("documents")
        }
    finally:
        database.engine.dispose()


def test_phase2_migration_enforces_batch_item_constraints(database: Database) -> None:
    inspector = inspect(database.engine)
    batch_item_foreign_keys = {
        foreign_key["constrained_columns"][0]: foreign_key["options"].get("ondelete")
        for foreign_key in inspector.get_foreign_keys("batch_items")
    }

    assert batch_item_foreign_keys == {
        "batch_id": "CASCADE",
        "existing_document_id": "SET NULL",
        "import_job_id": "SET NULL",
    }
    assert {"uq_batch_items_batch_source_identity", "uq_batch_items_import_job_id"} <= {
        constraint["name"] for constraint in inspector.get_unique_constraints("batch_items")
    }
