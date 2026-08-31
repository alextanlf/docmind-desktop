from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import delete, select

from app.api.errors import DomainError
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


def utc_now() -> datetime:
    return datetime.now(UTC)


class RepositoryStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list(self) -> list[RepositoryRecord]:
        with self.database.session() as session:
            return list(session.scalars(select(RepositoryRecord).order_by(RepositoryRecord.created_at.desc())))

    def get(self, repository_id: str) -> RepositoryRecord | None:
        with self.database.session() as session:
            return session.get(RepositoryRecord, repository_id)

    def upsert_remote(
        self, *, yuque_id: str, name: str, description: str | None, yuque_url: str | None
    ) -> RepositoryRecord:
        with self.database.session() as session:
            record = session.scalar(select(RepositoryRecord).where(RepositoryRecord.yuque_id == yuque_id))
            if record is None:
                record = RepositoryRecord(
                    yuque_id=yuque_id,
                    name=name,
                    description=description,
                    yuque_url=yuque_url,
                )
                session.add(record)
            else:
                record.name = name
                record.description = description
                record.yuque_url = yuque_url
                record.updated_at = utc_now()
            session.flush()
            return record


class DocumentStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_for_repository(self, repository_id: str) -> list[DocumentRecord]:
        with self.database.session() as session:
            statement = (
                select(DocumentRecord)
                .where(DocumentRecord.repository_id == repository_id)
                .order_by(DocumentRecord.created_at.desc())
            )
            return list(session.scalars(statement))

    def get(self, document_id: str) -> DocumentRecord | None:
        with self.database.session() as session:
            return session.get(DocumentRecord, document_id)

    def find_by_source(self, repository_id: str, source_url: str) -> DocumentRecord | None:
        with self.database.session() as session:
            statement = select(DocumentRecord).where(
                DocumentRecord.repository_id == repository_id,
                DocumentRecord.source_url == source_url,
            )
            return session.scalar(statement)

    def save_with_chunks(self, document: DocumentRecord, chunks: list[DocumentChunkRecord]) -> None:
        with self.database.session() as session:
            saved_document = session.merge(document)
            saved_document.chunk_count = len(chunks)
            saved_document.updated_at = utc_now()
            session.flush()
            for chunk in chunks:
                chunk.document_id = saved_document.id
                chunk.repository_id = saved_document.repository_id
                session.merge(chunk)

    def replace_chunks(self, document_id: str, chunks: list[DocumentChunkRecord]) -> None:
        with self.database.session() as session:
            document = session.get(DocumentRecord, document_id)
            if document is None:
                return
            session.execute(delete(DocumentChunkRecord).where(DocumentChunkRecord.document_id == document_id))
            for chunk in chunks:
                chunk.document_id = document_id
                chunk.repository_id = document.repository_id
                session.add(chunk)
            document.chunk_count = len(chunks)
            document.updated_at = utc_now()

    def delete_local(self, document_id: str) -> None:
        with self.database.session() as session:
            document = session.get(DocumentRecord, document_id)
            if document is not None:
                session.delete(document)


class ImportJobStore:
    _INTERRUPTED_STATES = (ImportStatus.PARSING, ImportStatus.UPLOADING, ImportStatus.INDEXING)

    def __init__(self, database: Database) -> None:
        self.database = database

    def create(self, job: ImportJobRecord) -> ImportJobRecord:
        with self.database.session() as session:
            session.add(job)
            session.flush()
            return job

    def get(self, job_id: str) -> ImportJobRecord | None:
        with self.database.session() as session:
            return session.get(ImportJobRecord, job_id)

    def transition(
        self,
        job_id: str,
        *,
        expected: set[ImportStatus],
        target: ImportStatus,
        progress: int,
        message: str,
    ) -> ImportJobRecord:
        with self.database.session() as session:
            job = session.get(ImportJobRecord, job_id)
            if job is None or job.state not in expected:
                raise DomainError("IMPORT_STATE_CONFLICT", "导入任务状态冲突", 409)
            now = utc_now()
            job.state = target
            job.current_stage = target.value
            job.progress = progress
            job.message = message
            job.updated_at = now
            if target in self._INTERRUPTED_STATES and job.started_at is None:
                job.started_at = now
            if target in (ImportStatus.COMPLETED, ImportStatus.FAILED, ImportStatus.CANCELLED):
                job.completed_at = now
            session.flush()
            return job

    def fail(self, job_id: str, *, code: str, message: str, retryable: bool) -> ImportJobRecord:
        with self.database.session() as session:
            job = session.get(ImportJobRecord, job_id)
            if job is None:
                raise DomainError("IMPORT_STATE_CONFLICT", "导入任务状态冲突", 409)
            now = utc_now()
            job.state = ImportStatus.FAILED
            job.current_stage = ImportStatus.FAILED.value
            job.message = message
            job.error_code = code
            job.error_message = message
            job.retryable = retryable
            job.completed_at = now
            job.updated_at = now
            session.flush()
            return job

    def request_cancel(self, job_id: str) -> ImportJobRecord:
        with self.database.session() as session:
            job = session.get(ImportJobRecord, job_id)
            if job is None:
                raise DomainError("IMPORT_STATE_CONFLICT", "导入任务状态冲突", 409)
            job.cancel_requested = True
            job.updated_at = utc_now()
            session.flush()
            return job

    def recover_interrupted(self) -> int:
        with self.database.session() as session:
            statement = select(ImportJobRecord).where(ImportJobRecord.state.in_(self._INTERRUPTED_STATES))
            jobs = list(session.scalars(statement))
            now = utc_now()
            for job in jobs:
                job.state = ImportStatus.FAILED
                job.current_stage = ImportStatus.FAILED.value
                job.message = "应用重启导致任务中断"
                job.error_code = "APP_RESTARTED"
                job.error_message = "应用重启导致任务中断"
                job.retryable = True
                job.completed_at = now
                job.updated_at = now
            return len(jobs)


class ConversationStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_session(self, repository_ids: list[str]) -> SessionRecord:
        with self.database.session() as session:
            record = SessionRecord(repository_scope_json=json.dumps(repository_ids))
            session.add(record)
            session.flush()
            return record

    def list_sessions(self) -> list[SessionRecord]:
        with self.database.session() as session:
            return list(session.scalars(select(SessionRecord).order_by(SessionRecord.updated_at.desc())))

    def list_messages(self, session_id: str) -> list[MessageRecord]:
        with self.database.session() as session:
            statement = select(MessageRecord).where(MessageRecord.session_id == session_id).order_by(
                MessageRecord.created_at.asc()
            )
            return list(session.scalars(statement))

    def add_message(self, message: MessageRecord) -> MessageRecord:
        with self.database.session() as session:
            session.add(message)
            session.flush()
            return message


class SettingStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def get(self, key: str) -> str | None:
        with self.database.session() as session:
            record = session.get(SettingRecord, key)
            return record.value if record is not None else None

    def set(self, key: str, value: str) -> None:
        with self.database.session() as session:
            record = session.get(SettingRecord, key)
            if record is None:
                session.add(SettingRecord(key=key, value=value))
            else:
                record.value = value
                record.updated_at = utc_now()
