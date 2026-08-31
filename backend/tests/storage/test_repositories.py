from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text

from app.api.errors import DomainError
from app.config import AppSettings
from app.main import create_app
from app.storage.database import Database
from app.storage.models import (
    DocumentChunkRecord,
    DocumentRecord,
    ImportJobRecord,
    ImportStatus,
    MessageRecord,
    RepositoryRecord,
)
from app.storage.repositories import (
    ConversationStore,
    DocumentStore,
    ImportJobStore,
    RepositoryStore,
    SettingStore,
)


def test_repository_upsert_updates_remote_record_without_duplicate(database: Database) -> None:
    store = RepositoryStore(database)
    first = store.upsert_remote(
        yuque_id="yuque-1", name="SwiftUI", description="first", yuque_url="https://yuque/1"
    )
    second = store.upsert_remote(
        yuque_id="yuque-1", name="SwiftUI updated", description=None, yuque_url=None
    )

    assert second.id == first.id
    assert [(record.yuque_id, record.name, record.description, record.yuque_url) for record in store.list()] == [
        ("yuque-1", "SwiftUI updated", None, None)
    ]
    assert store.get(first.id).id == first.id  # type: ignore[union-attr]


def test_document_store_replaces_chunks_and_finds_source(database: Database) -> None:
    repository = RepositoryRecord(id="repo-1", name="SwiftUI")
    document = DocumentRecord(
        id="document-1",
        repository_id=repository.id,
        title="State",
        source_url="https://yuque/state",
    )
    first_chunk = DocumentChunkRecord(
        id="chunk-1",
        document_id=document.id,
        repository_id=repository.id,
        chunk_index=0,
        text="first",
        token_count=1,
    )
    with database.session() as session:
        session.add(repository)

    store = DocumentStore(database)
    store.save_with_chunks(document, [first_chunk])
    store.replace_chunks(
        document.id,
        [
            DocumentChunkRecord(
                id="chunk-2",
                document_id=document.id,
                repository_id=repository.id,
                chunk_index=0,
                text="replacement",
                token_count=1,
            )
        ],
    )

    assert store.find_by_source(repository.id, "https://yuque/state").id == document.id  # type: ignore[union-attr]
    assert [record.id for record in store.list_for_repository(repository.id)] == [document.id]
    with database.session() as session:
        assert session.get(DocumentChunkRecord, "chunk-1") is None
        assert session.get(DocumentChunkRecord, "chunk-2").text == "replacement"  # type: ignore[union-attr]


def test_import_job_transitions_only_from_expected_state(database: Database) -> None:
    store = ImportJobStore(database)
    job = store.create(ImportJobRecord(id="job-1", source_kind="url", source_value="https://example"))

    transitioned = store.transition(
        job.id,
        expected={ImportStatus.PENDING},
        target=ImportStatus.PARSING,
        progress=15,
        message="Parsing",
    )

    assert (transitioned.state, transitioned.progress, transitioned.message) == (
        ImportStatus.PARSING,
        15,
        "Parsing",
    )
    with pytest.raises(DomainError) as error:
        store.transition(
            job.id,
            expected={ImportStatus.PENDING},
            target=ImportStatus.INDEXING,
            progress=80,
            message="Indexing",
        )
    assert error.value.code == "IMPORT_STATE_CONFLICT"


def test_import_job_persists_status_enum_values(database: Database) -> None:
    store = ImportJobStore(database)
    store.create(ImportJobRecord(id="job-status", source_kind="url", source_value="https://example"))

    with database.session() as session:
        state = session.scalar(text("SELECT state FROM import_jobs WHERE id = 'job-status'"))

    assert state == "pending"


def test_import_job_failure_cancel_and_restart_recovery(database: Database) -> None:
    store = ImportJobStore(database)
    failed = store.create(ImportJobRecord(id="failed", source_kind="url", source_value="https://failed"))
    active = store.create(
        ImportJobRecord(
            id="active",
            source_kind="url",
            source_value="https://active",
            state=ImportStatus.UPLOADING,
            progress=50,
        )
    )
    cancelled = store.create(ImportJobRecord(id="cancel", source_kind="url", source_value="https://cancel"))

    assert store.request_cancel(cancelled.id).cancel_requested is True
    assert store.fail(failed.id, code="PARSE_ERROR", message="Invalid PDF", retryable=False).state == ImportStatus.FAILED
    assert store.recover_interrupted() == 1
    recovered = store.get(active.id)

    assert (recovered.state, recovered.error_code, recovered.retryable) == (  # type: ignore[union-attr]
        ImportStatus.FAILED,
        "APP_RESTARTED",
        True,
    )
    assert store.recover_interrupted() == 0


def test_conversations_preserve_scope_and_message_order(database: Database) -> None:
    store = ConversationStore(database)
    session = store.create_session(["repo-2", "repo-1"])
    store.add_message(MessageRecord(id="message-1", session_id=session.id, role="user", content="First"))
    store.add_message(MessageRecord(id="message-2", session_id=session.id, role="assistant", content="Second"))

    assert json.loads(session.repository_scope_json) == ["repo-2", "repo-1"]
    assert [record.id for record in store.list_sessions()] == [session.id]
    assert [record.content for record in store.list_messages(session.id)] == ["First", "Second"]


def test_setting_store_overwrites_values(database: Database) -> None:
    store = SettingStore(database)

    store.set("language", "zh-CN")
    store.set("language", "en")

    assert store.get("language") == "en"
    assert store.get("missing") is None


def test_app_lifespan_migrates_and_recovers_interrupted_jobs(tmp_path: Path) -> None:
    data_dir = tmp_path / "docmind-data"
    settings = AppSettings(
        session_token=SecretStr("test-runtime-token"), data_dir=data_dir, environment="test"
    )
    database = Database(f"sqlite+pysqlite:///{data_dir / 'database' / 'docmind.sqlite3'}")
    database.upgrade()
    ImportJobStore(database).create(
        ImportJobRecord(
            id="interrupted",
            source_kind="url",
            source_value="https://interrupted",
            state=ImportStatus.INDEXING,
        )
    )

    with TestClient(create_app(settings)) as client:
        assert client.app.state.database is not None

    recovered = ImportJobStore(database).get("interrupted")
    assert (recovered.state, recovered.error_code, recovered.retryable) == (  # type: ignore[union-attr]
        ImportStatus.FAILED,
        "APP_RESTARTED",
        True,
    )
    database.engine.dispose()
