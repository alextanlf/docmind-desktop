from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import StatementError

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
    SessionRecord,
    SettingRecord,
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
    assert store.vector_ids(document.id) == ["chunk-2"]


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


def test_import_job_reservation_rejects_matching_active_fingerprint(database: Database) -> None:
    with database.session() as session:
        session.add(RepositoryRecord(id="repo-reserve", name="Repository"))
    store = ImportJobStore(database)
    metadata = json.dumps({"version": 1, "value": "source", "fingerprint": "same-hash"})

    first = store.reserve(
        ImportJobRecord(
            id="reserved-first",
            source_kind="url",
            source_value=metadata,
            repository_id="repo-reserve",
        ),
        fingerprint="same-hash",
    )

    with pytest.raises(DomainError) as error:
        store.reserve(
            ImportJobRecord(
                id="reserved-second",
                source_kind="url",
                source_value=metadata,
                repository_id="repo-reserve",
            ),
            fingerprint="same-hash",
        )

    assert first.id == "reserved-first"
    assert error.value.code == "IMPORT_ALREADY_RUNNING"
    assert [job.id for job in store.list()] == ["reserved-first"]


def test_timestamp_records_round_trip_as_utc_aware_values(database: Database) -> None:
    offset_time = datetime(2026, 8, 31, 20, 0, tzinfo=timezone(timedelta(hours=8)))
    with database.session() as session:
        repository = RepositoryRecord(id="repo-times", name="Repository")
        document = DocumentRecord(id="document-times", repository_id=repository.id, title="Document")
        session_record = SessionRecord(id="session-times")
        session.add_all([repository, document, session_record])
        session.flush()
        session.add_all(
            [
                DocumentChunkRecord(
                    id="chunk-times",
                    document_id=document.id,
                    repository_id=repository.id,
                    chunk_index=0,
                    text="chunk",
                    token_count=1,
                ),
                ImportJobRecord(
                    id="job-times",
                    source_kind="url",
                    source_value="https://example.test",
                    started_at=offset_time,
                    completed_at=offset_time,
                ),
                MessageRecord(id="message-times", session_id=session_record.id, role="user", content="Hello"),
                SettingRecord(key="timezone", value="UTC"),
            ]
        )

    with database.session() as session:
        timestamp_values = [
            session.get(RepositoryRecord, "repo-times").created_at,
            session.get(RepositoryRecord, "repo-times").updated_at,
            session.get(DocumentRecord, "document-times").created_at,
            session.get(DocumentRecord, "document-times").updated_at,
            session.get(DocumentChunkRecord, "chunk-times").created_at,
            session.get(ImportJobRecord, "job-times").created_at,
            session.get(ImportJobRecord, "job-times").started_at,
            session.get(ImportJobRecord, "job-times").completed_at,
            session.get(ImportJobRecord, "job-times").updated_at,
            session.get(SessionRecord, "session-times").created_at,
            session.get(SessionRecord, "session-times").updated_at,
            session.get(MessageRecord, "message-times").created_at,
            session.get(SettingRecord, "timezone").updated_at,
        ]

    assert all(value.tzinfo is UTC and value.utcoffset() == timedelta(0) for value in timestamp_values)
    assert timestamp_values[6] == datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    assert timestamp_values[7] == datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def test_timestamp_records_reject_naive_values(database: Database) -> None:
    with pytest.raises(StatementError, match="timezone-aware"), database.session() as session:
        session.add(
            RepositoryRecord(
                id="repo-naive-time",
                name="Repository",
                created_at=datetime.fromisoformat("2026-08-31T12:00:00"),
            )
        )


def test_import_job_failure_cancel_and_restart_recovery(database: Database) -> None:
    store = ImportJobStore(database)
    failed = store.create(
        ImportJobRecord(
            id="failed",
            source_kind="url",
            source_value="https://failed",
            state=ImportStatus.PARSING,
            current_stage=ImportStatus.PARSING.value,
        )
    )
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


def test_import_job_retry_falls_back_from_legacy_failed_stage(database: Database) -> None:
    store = ImportJobStore(database)
    store.create(
        ImportJobRecord(
            id="legacy-failed",
            source_kind="url",
            source_value="https://failed",
            state=ImportStatus.FAILED,
            current_stage=ImportStatus.FAILED.value,
            retryable=True,
        )
    )

    retried = store.reset_for_retry("legacy-failed")

    assert retried.state == ImportStatus.PENDING


def test_import_job_retry_falls_back_from_unknown_stage(database: Database) -> None:
    store = ImportJobStore(database)
    store.create(
        ImportJobRecord(
            id="unknown-stage",
            source_kind="url",
            source_value="https://failed",
            state=ImportStatus.FAILED,
            current_stage="legacy-transforming",
            retryable=True,
        )
    )

    retried = store.reset_for_retry("unknown-stage")

    assert retried.state == ImportStatus.PENDING


def test_restart_recovery_finishes_requested_cancellation(database: Database) -> None:
    store = ImportJobStore(database)
    store.create(
        ImportJobRecord(
            id="cancel-on-restart",
            source_kind="url",
            source_value="https://cancelled",
            state=ImportStatus.INDEXING,
            current_stage=ImportStatus.INDEXING.value,
            progress=90,
            cancel_requested=True,
        )
    )

    assert store.recover_interrupted() == 1
    recovered = store.get("cancel-on-restart")

    assert recovered is not None
    assert recovered.state == ImportStatus.CANCELLED
    assert recovered.message == "导入已取消"
    assert recovered.error_code is None
    assert recovered.retryable is False


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
