from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

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


def test_phase2_migration_rejects_invalid_batch_enum_values(database: Database) -> None:
    with pytest.raises(IntegrityError), database.session() as session:
        session.execute(text("INSERT INTO batch_imports (id, source_kind) VALUES ('bad-kind', 'not_a_kind')"))

    with pytest.raises(IntegrityError), database.session() as session:
        session.execute(
            text(
                "INSERT INTO batch_imports (id, source_kind, state) "
                "VALUES ('bad-state', 'staged_directory', 'not_a_state')"
            )
        )

    with database.session() as session:
        session.execute(
            text("INSERT INTO batch_imports (id, source_kind) VALUES ('item-enums', 'staged_directory')")
        )

    with pytest.raises(IntegrityError), database.session() as session:
        session.execute(
            text(
                "INSERT INTO batch_items "
                "(id, batch_id, ordinal, source_identity, source_revision, title, display_path, media_type, "
                "size_bytes, state) VALUES "
                "('bad-item-state', 'item-enums', 0, 'source-a', 'revision-a', 'Item', 'item.md', "
                "'text/markdown', 1, 'not_an_item_state')"
            )
        )

    with pytest.raises(IntegrityError), database.session() as session:
        session.execute(
            text(
                "INSERT INTO batch_items "
                "(id, batch_id, ordinal, source_identity, source_revision, title, display_path, media_type, "
                "size_bytes, decision) VALUES "
                "('bad-decision', 'item-enums', 1, 'source-b', 'revision-b', 'Item', 'item.md', "
                "'text/markdown', 1, 'not_a_decision')"
            )
        )


def test_phase2_migration_enforces_remote_binding_and_partial_source_identity_index(
    database: Database,
) -> None:
    with database.session() as session:
        session.execute(text("INSERT INTO repositories (id, name) VALUES ('repo-1', 'Repository')"))
        session.execute(
            text(
                "INSERT INTO documents (id, repository_id, title, source_identity) "
                "VALUES ('document-null-a', 'repo-1', 'A', NULL), ('document-null-b', 'repo-1', 'B', NULL)"
            )
        )
        session.execute(
            text(
                "INSERT INTO documents (id, repository_id, title, source_identity) "
                "VALUES ('document-identity-a', 'repo-1', 'C', 'folder:r:a.md')"
            )
        )
        session.execute(
            text("INSERT INTO batch_imports (id, source_kind) VALUES ('batch-1', 'staged_directory')")
        )

    with pytest.raises(IntegrityError), database.session() as session:
        session.execute(
            text(
                "INSERT INTO documents (id, repository_id, title, source_identity) "
                "VALUES ('document-identity-b', 'repo-1', 'D', 'folder:r:a.md')"
            )
        )

    with pytest.raises(IntegrityError), database.session() as session:
        session.execute(
            text(
                "INSERT INTO batch_items "
                "(id, batch_id, ordinal, source_identity, source_revision, title, display_path, media_type, "
                "size_bytes, decision, remote_binding_json) VALUES "
                "('item-1', 'batch-1', 0, 'source-1', 'revision-1', 'Item', 'item.md', "
                "'text/markdown', 1, 'attach_remote', '{}')"
            )
        )

    with pytest.raises(IntegrityError), database.session() as session:
        session.execute(
            text(
                "INSERT INTO batch_items "
                "(id, batch_id, ordinal, source_identity, source_revision, title, display_path, media_type, "
                "size_bytes, decision, remote_binding_json) VALUES "
                "('item-2', 'batch-1', 1, 'source-2', 'revision-2', 'Item', 'item.md', "
                "'text/markdown', 1, 'attach_remote', 'not-json')"
            )
        )
