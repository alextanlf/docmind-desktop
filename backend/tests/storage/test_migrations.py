from __future__ import annotations

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.storage.database import Database
from app.storage.models import DocumentChunkRecord, DocumentRecord, RepositoryRecord
from app.storage.repositories import DocumentStore


def test_upgrade_creates_all_phase_one_and_phase_two_tables() -> None:
    database = Database("sqlite+pysqlite:///:memory:")
    database.upgrade()

    names = set(inspect(database.engine).get_table_names())

    expected = {
        "alembic_version",
        "repositories",
        "documents",
        "document_chunks",
        "import_jobs",
        "sessions",
        "chat_requests",
        "messages",
        "settings",
        "vector_cleanups",
        "document_mutations",
        "batch_imports",
        "batch_items",
        # Phase 2B remote discovery frontier (migration 0006).
        "crawl_entries",
    }
    # Keep this assertion forward-compatible with additive migrations while
    # still enforcing that every table required by the current schema exists.
    assert expected.issubset(names)
    database.engine.dispose()


def test_upgrade_is_idempotent(database: Database) -> None:
    database.upgrade()

    assert inspect(database.engine).get_table_names().count("documents") == 1


def test_upgrade_creates_exact_phase_one_columns(database: Database) -> None:
    inspector = inspect(database.engine)

    assert {column["name"] for column in inspector.get_columns("repositories")} == {
        "id", "yuque_id", "name", "description", "yuque_url", "sync_status", "document_count",
        "created_at", "updated_at",
    }
    assert {column["name"] for column in inspector.get_columns("documents")} == {
        "id", "repository_id", "yuque_id", "title", "source_url", "raw_path", "markdown_path",
        "source_type", "content_hash", "chunk_count", "status", "yuque_url", "source_identity",
        "source_revision", "created_at", "updated_at",
    }
    assert {column["name"] for column in inspector.get_columns("document_chunks")} == {
        "id", "document_id", "repository_id", "chunk_index", "text", "section_path", "page_number",
        "token_count", "source_url", "vector_id", "created_at",
    }
    assert {column["name"] for column in inspector.get_columns("import_jobs")} == {
        "id", "source_kind", "source_value", "repository_id", "state", "current_stage", "progress",
        "message", "error_code", "error_message", "retryable", "document_id", "cancel_requested",
        "created_at", "started_at", "completed_at", "updated_at",
    }
    assert {column["name"] for column in inspector.get_columns("sessions")} >= {
        "id", "title", "repository_scope_json", "created_at", "updated_at",
    }
    assert {column["name"] for column in inspector.get_columns("chat_requests")} == {
        "request_id", "session_id", "terminal_type", "terminal_payload_json", "created_at", "updated_at",
    }
    assert {column["name"] for column in inspector.get_columns("messages")} == {
        "id", "session_id", "role", "content", "citations_json", "generation_status", "created_at",
    }
    assert {column["name"] for column in inspector.get_columns("settings")} == {
        "key", "value", "updated_at",
    }


def test_migrated_schema_supplies_defaults_for_raw_inserts(database: Database) -> None:
    with database.session() as session:
        session.execute(text("INSERT INTO repositories (id, name) VALUES ('repo-defaults', 'Repository')"))
        session.execute(
            text(
                "INSERT INTO documents (id, repository_id, title) "
                "VALUES ('document-defaults', 'repo-defaults', 'Document')"
            )
        )
        session.execute(
            text(
                "INSERT INTO document_chunks "
                "(id, document_id, repository_id, chunk_index, text, token_count) "
                "VALUES ('chunk-defaults', 'document-defaults', 'repo-defaults', 0, 'chunk', 1)"
            )
        )
        session.execute(
            text(
                "INSERT INTO import_jobs (id, source_kind, source_value) "
                "VALUES ('job-defaults', 'url', 'https://example.test')"
            )
        )
        session.execute(text("INSERT INTO sessions (id) VALUES ('session-defaults')"))
        session.execute(
            text(
                "INSERT INTO messages (id, session_id, role, content) "
                "VALUES ('message-defaults', 'session-defaults', 'user', 'Hello')"
            )
        )
        session.execute(text("INSERT INTO settings (key, value) VALUES ('language', 'en')"))

        repository = session.execute(
            text("SELECT sync_status, document_count, created_at, updated_at FROM repositories")
        ).one()
        document = session.execute(
            text("SELECT source_type, chunk_count, status, created_at, updated_at FROM documents")
        ).one()
        job = session.execute(
            text(
                "SELECT state, progress, message, retryable, cancel_requested, created_at, updated_at "
                "FROM import_jobs"
            )
        ).one()
        conversation = session.execute(
            text("SELECT repository_scope_json, created_at, updated_at FROM sessions")
        ).one()
        message = session.execute(
            text("SELECT citations_json, generation_status, created_at FROM messages")
        ).one()
        chunk_created_at = session.scalar(text("SELECT created_at FROM document_chunks"))
        setting_updated_at = session.scalar(text("SELECT updated_at FROM settings"))

    assert repository[:2] == ("unknown", 0)
    assert document[:3] == ("remote", 0, "pending")
    assert job[:5] == ("pending", 0, "", 0, 0)
    assert conversation[0] == "[]"
    assert message[:2] == ("[]", "completed")
    assert all(value is not None for value in (*repository[2:], *document[3:], *job[5:]))
    assert all(value is not None for value in (*conversation[1:], *message[2:], chunk_created_at, setting_updated_at))


def test_migration_declares_defaults_indexes_and_foreign_key_actions(database: Database) -> None:
    inspector = inspect(database.engine)
    defaultable_columns = {
        "repositories": {"sync_status", "document_count", "created_at", "updated_at"},
        "documents": {"source_type", "chunk_count", "status", "created_at", "updated_at"},
        "document_chunks": {"created_at"},
        "import_jobs": {
            "state", "progress", "message", "retryable", "cancel_requested", "created_at", "updated_at"
        },
        "sessions": {"repository_scope_json", "created_at", "updated_at"},
        "chat_requests": {"created_at", "updated_at"},
        "messages": {"citations_json", "generation_status", "created_at"},
        "settings": {"updated_at"},
    }

    for table_name, column_names in defaultable_columns.items():
        columns = {column["name"]: column for column in inspector.get_columns(table_name)}
        assert all(columns[name]["default"] is not None for name in column_names)

    index_columns = {
        "documents": {
            "ix_documents_repository_id": ["repository_id"],
            "ix_documents_source_url": ["source_url"],
            "uq_documents_repository_source_identity": ["repository_id", "source_identity"],
        },
        "document_chunks": {
            "ix_document_chunks_document_id": ["document_id"],
            "ix_document_chunks_repository_id": ["repository_id"],
        },
        "import_jobs": {"ix_import_jobs_state": ["state"]},
        "chat_requests": {"ix_chat_requests_session_id": ["session_id"]},
        "messages": {"ix_messages_session_id": ["session_id"]},
    }
    for table_name, expected_indexes in index_columns.items():
        indexes = {index["name"]: index["column_names"] for index in inspector.get_indexes(table_name)}
        assert indexes == expected_indexes

    foreign_keys = {
        table_name: {
            foreign_key["constrained_columns"][0]: foreign_key["options"].get("ondelete")
            for foreign_key in inspector.get_foreign_keys(table_name)
        }
        for table_name in ("documents", "document_chunks", "import_jobs", "chat_requests", "messages")
    }
    assert foreign_keys == {
        "documents": {"repository_id": "CASCADE"},
        "document_chunks": {"document_id": "CASCADE", "repository_id": "CASCADE"},
        "import_jobs": {"repository_id": "SET NULL", "document_id": "SET NULL"},
        "chat_requests": {"session_id": "CASCADE"},
        "messages": {"session_id": "CASCADE"},
    }


def test_document_delete_cascades_chunks(database: Database) -> None:
    with database.session() as session:
        repo = RepositoryRecord(id="repo-1", name="SwiftUI")
        doc = DocumentRecord(id="doc-1", repository_id=repo.id, title="State")
        chunk = DocumentChunkRecord(
            id="chunk-1",
            document_id=doc.id,
            repository_id=repo.id,
            chunk_index=0,
            text="@State",
            token_count=1,
        )
        session.add_all([repo, doc, chunk])

    DocumentStore(database).delete_local("doc-1")

    with database.session() as session:
        assert session.get(DocumentChunkRecord, "chunk-1") is None


def test_foreign_keys_reject_chunk_for_missing_document(database: Database) -> None:
    with pytest.raises(IntegrityError), database.session() as session:
        session.add(RepositoryRecord(id="repo-1", name="SwiftUI"))
        session.add(
            DocumentChunkRecord(
                id="orphan",
                document_id="missing-document",
                repository_id="repo-1",
                chunk_index=0,
                text="orphan",
                token_count=1,
            )
        )


def test_session_commits_successful_work_and_rolls_back_failures(database: Database) -> None:
    with database.session() as session:
        session.add(RepositoryRecord(id="committed", name="Committed"))

    with pytest.raises(RuntimeError), database.session() as session:
        session.add(RepositoryRecord(id="rolled-back", name="Rolled back"))
        raise RuntimeError("stop")

    with database.session() as session:
        assert session.get(RepositoryRecord, "committed") is not None
        assert session.get(RepositoryRecord, "rolled-back") is None
