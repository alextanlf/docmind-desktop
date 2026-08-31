from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select

from app.api.errors import DomainError
from app.imports.state_machine import ensure_transition_allowed
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
    VectorCleanupRecord,
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

    def set_document_count(self, repository_id: str, count: int) -> None:
        with self.database.session() as session:
            record = session.get(RepositoryRecord, repository_id)
            if record is not None:
                record.document_count = count
                record.updated_at = utc_now()

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

    def create(self, document: DocumentRecord) -> DocumentRecord:
        with self.database.session() as session:
            session.add(document)
            session.flush()
            return document

    def find_by_source(self, repository_id: str, source_url: str) -> DocumentRecord | None:
        with self.database.session() as session:
            statement = select(DocumentRecord).where(
                DocumentRecord.repository_id == repository_id,
                DocumentRecord.source_url == source_url,
            )
            return session.scalar(statement)

    def find_by_hash(self, repository_id: str, content_hash: str) -> DocumentRecord | None:
        with self.database.session() as session:
            statement = select(DocumentRecord).where(
                DocumentRecord.repository_id == repository_id,
                DocumentRecord.content_hash == content_hash,
            )
            return session.scalar(statement)

    def update_import_metadata(
        self,
        document_id: str,
        *,
        title: str,
        source_url: str,
        raw_path: str,
        markdown_path: str,
        content_hash: str,
    ) -> DocumentRecord:
        with self.database.session() as session:
            document = session.get(DocumentRecord, document_id)
            if document is None:
                raise DomainError("IMPORT_STATE_CONFLICT", "导入文档不存在", 409)
            document.title = title
            document.source_url = source_url
            document.raw_path = raw_path
            document.markdown_path = markdown_path
            document.content_hash = content_hash
            document.source_type = "import"
            document.updated_at = utc_now()
            session.flush()
            return document

    def update_remote(
        self, document_id: str, *, yuque_id: str, yuque_url: str | None
    ) -> DocumentRecord:
        with self.database.session() as session:
            document = session.get(DocumentRecord, document_id)
            if document is None:
                raise DomainError("IMPORT_STATE_CONFLICT", "导入文档不存在", 409)
            document.yuque_id = yuque_id
            document.yuque_url = yuque_url
            document.status = "uploaded"
            document.updated_at = utc_now()
            session.flush()
            return document

    def update_editor(
        self,
        document_id: str,
        *,
        title: str,
        yuque_id: str | None,
        yuque_url: str | None,
        markdown_path: str,
        source_url: str | None,
    ) -> DocumentRecord:
        with self.database.session() as session:
            document = session.get(DocumentRecord, document_id)
            if document is None:
                raise DomainError("NOT_FOUND", "资源不存在", 404)
            document.title = title
            document.yuque_id = yuque_id
            document.yuque_url = yuque_url
            document.markdown_path = markdown_path
            document.source_url = source_url
            document.updated_at = utc_now()
            session.flush()
            return document

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

    def vector_ids(self, document_id: str) -> list[str]:
        with self.database.session() as session:
            statement = (
                select(DocumentChunkRecord)
                .where(DocumentChunkRecord.document_id == document_id)
                .order_by(DocumentChunkRecord.chunk_index)
            )
            chunks = list(session.scalars(statement))
            return [chunk.vector_id or chunk.id for chunk in chunks]

    def list_chunks(self, document_id: str) -> list[DocumentChunkRecord]:
        with self.database.session() as session:
            statement = select(DocumentChunkRecord).where(
                DocumentChunkRecord.document_id == document_id
            ).order_by(DocumentChunkRecord.chunk_index)
            return list(session.scalars(statement))

    def delete_local(self, document_id: str) -> None:
        with self.database.session() as session:
            document = session.get(DocumentRecord, document_id)
            if document is not None:
                session.delete(document)


class ImportJobStore:
    _INTERRUPTED_STATES = (ImportStatus.PARSING, ImportStatus.UPLOADING, ImportStatus.INDEXING)
    _ACTIVE_STATES = (ImportStatus.PENDING, *_INTERRUPTED_STATES)

    def __init__(self, database: Database) -> None:
        self.database = database

    def create(self, job: ImportJobRecord) -> ImportJobRecord:
        with self.database.session() as session:
            session.add(job)
            session.flush()
            return job

    def create_cleanup(self, *, repository_id: str, document_id: str, vector_ids: list[str]) -> ImportJobRecord:
        payload = json.dumps({"repository_id": repository_id, "document_id": document_id, "vector_ids": vector_ids})
        return self.create(
            ImportJobRecord(id=str(uuid4()), source_kind="api_cleanup", source_value=payload, repository_id=repository_id, document_id=document_id)
        )

    def list_cleanups(self) -> list[ImportJobRecord]:
        with self.database.session() as session:
            return list(session.scalars(select(ImportJobRecord).where(ImportJobRecord.source_kind == "api_cleanup")))

    def delete_job(self, job_id: str) -> None:
        with self.database.session() as session:
            record = session.get(ImportJobRecord, job_id)
            if record is not None:
                session.delete(record)

    def reserve(self, job: ImportJobRecord, *, fingerprint: str) -> ImportJobRecord:
        with self.database.session() as session:
            statement = select(ImportJobRecord).where(
                ImportJobRecord.repository_id == job.repository_id,
                ImportJobRecord.state.in_(self._ACTIVE_STATES),
            )
            for active in session.scalars(statement):
                if _source_fingerprint(active.source_value) == fingerprint:
                    raise DomainError("IMPORT_ALREADY_RUNNING", "导入任务正在运行", 409)
            session.add(job)
            session.flush()
            return job

    def get(self, job_id: str) -> ImportJobRecord | None:
        with self.database.session() as session:
            return session.get(ImportJobRecord, job_id)

    def list(self) -> list[ImportJobRecord]:
        with self.database.session() as session:
            return list(session.scalars(select(ImportJobRecord).order_by(ImportJobRecord.created_at)))

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
            ensure_transition_allowed(job.state, target)
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

    def update_progress(
        self, job_id: str, *, expected: ImportStatus, progress: int, message: str
    ) -> ImportJobRecord:
        with self.database.session() as session:
            job = session.get(ImportJobRecord, job_id)
            if job is None or job.state != expected:
                raise DomainError("IMPORT_STATE_CONFLICT", "导入任务状态冲突", 409)
            job.progress = progress
            job.message = message
            job.updated_at = utc_now()
            session.flush()
            return job

    def attach_document(self, job_id: str, document_id: str) -> ImportJobRecord:
        with self.database.session() as session:
            job = session.get(ImportJobRecord, job_id)
            if job is None:
                raise DomainError("IMPORT_STATE_CONFLICT", "导入任务状态冲突", 409)
            job.document_id = document_id
            job.updated_at = utc_now()
            session.flush()
            return job

    def merge_source_metadata(
        self, job_id: str, updates: dict[str, Any]
    ) -> ImportJobRecord:
        with self.database.session() as session:
            job = session.get(ImportJobRecord, job_id)
            if job is None:
                raise DomainError("IMPORT_STATE_CONFLICT", "导入任务状态冲突", 409)
            metadata = _normalized_source_metadata(job)
            requested_sequence = updates.get("last_event_sequence")
            if isinstance(requested_sequence, int) and not isinstance(
                requested_sequence, bool
            ):
                updates = {
                    **updates,
                    "last_event_sequence": max(
                        metadata["last_event_sequence"], requested_sequence
                    ),
                }
            metadata.update(updates)
            metadata["version"] = 2
            job.source_value = _dump_source_metadata(metadata)
            job.updated_at = utc_now()
            session.flush()
            return job

    def allocate_event_sequence(self, job_id: str) -> int:
        with self.database.session() as session:
            job = session.get(ImportJobRecord, job_id)
            if job is None:
                raise DomainError("IMPORT_STATE_CONFLICT", "导入任务状态冲突", 409)
            metadata = _normalized_source_metadata(job)
            current = metadata["last_event_sequence"]
            sequence = current + 1
            metadata["last_event_sequence"] = sequence
            job.source_value = _dump_source_metadata(metadata)
            job.updated_at = utc_now()
            session.flush()
            return sequence

    def update_stale_vector_ids(self, job_id: str, vector_ids: list[str]) -> None:
        with self.database.session() as session:
            job = session.get(ImportJobRecord, job_id)
            if job is None:
                raise DomainError("IMPORT_STATE_CONFLICT", "导入任务状态冲突", 409)
            metadata = _normalized_source_metadata(job)
            metadata["stale_vector_ids"] = list(dict.fromkeys(vector_ids))
            job.source_value = _dump_source_metadata(metadata)
            job.updated_at = utc_now()
            session.flush()

    def fail(self, job_id: str, *, code: str, message: str, retryable: bool) -> ImportJobRecord:
        with self.database.session() as session:
            job = session.get(ImportJobRecord, job_id)
            if job is None or job.state not in self._INTERRUPTED_STATES:
                raise DomainError("IMPORT_STATE_CONFLICT", "导入任务状态冲突", 409)
            ensure_transition_allowed(job.state, ImportStatus.FAILED)
            now = utc_now()
            job.state = ImportStatus.FAILED
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
            if job is None or job.state in (
                ImportStatus.COMPLETED,
                ImportStatus.FAILED,
                ImportStatus.CANCELLED,
            ):
                raise DomainError("IMPORT_STATE_CONFLICT", "导入任务状态冲突", 409)
            job.cancel_requested = True
            job.updated_at = utc_now()
            session.flush()
            return job

    def cancel(self, job_id: str) -> ImportJobRecord:
        job = self.get(job_id)
        if job is None:
            raise DomainError("IMPORT_STATE_CONFLICT", "导入任务状态冲突", 409)
        return self.transition(
            job_id,
            expected={job.state},
            target=ImportStatus.CANCELLED,
            progress=job.progress,
            message="导入已取消",
        )

    def reset_for_retry(self, job_id: str) -> ImportJobRecord:
        with self.database.session() as session:
            job = session.get(ImportJobRecord, job_id)
            if job is None or job.state != ImportStatus.FAILED or not job.retryable:
                raise DomainError("IMPORT_STATE_CONFLICT", "导入任务不可重试", 409)
            try:
                resume_stage = ImportStatus(job.current_stage or ImportStatus.PARSING.value)
            except ValueError:
                resume_stage = ImportStatus.PARSING
            if resume_stage not in self._INTERRUPTED_STATES:
                resume_stage = ImportStatus.PARSING
            job.state = (
                ImportStatus.PENDING if resume_stage == ImportStatus.PARSING else resume_stage
            )
            job.error_code = None
            job.error_message = None
            job.retryable = False
            job.cancel_requested = False
            job.completed_at = None
            job.updated_at = utc_now()
            session.flush()
            return job

    def recover_interrupted(self) -> int:
        with self.database.session() as session:
            statement = select(ImportJobRecord).where(ImportJobRecord.state.in_(self._INTERRUPTED_STATES))
            jobs = list(session.scalars(statement))
            now = utc_now()
            for job in jobs:
                job.current_stage = job.current_stage or job.state.value
                if job.cancel_requested:
                    job.state = ImportStatus.CANCELLED
                    job.message = "导入已取消"
                    job.error_code = None
                    job.error_message = None
                    job.retryable = False
                else:
                    job.state = ImportStatus.FAILED
                    job.message = "应用重启导致任务中断"
                    job.error_code = "APP_RESTARTED"
                    job.error_message = "应用重启导致任务中断"
                    job.retryable = True
                job.completed_at = now
                job.updated_at = now
            return len(jobs)


def _source_fingerprint(source_value: str) -> str | None:
    try:
        metadata = json.loads(source_value)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(metadata, dict):
        return None
    fingerprint = metadata.get("fingerprint")
    return fingerprint if isinstance(fingerprint, str) else None


def _normalized_source_metadata(job: ImportJobRecord) -> dict[str, Any]:
    try:
        decoded = json.loads(job.source_value)
    except (json.JSONDecodeError, TypeError):
        decoded = None
    if (
        isinstance(decoded, dict)
        and decoded.get("version") in {1, 2}
        and isinstance(decoded.get("value"), str)
    ):
        metadata = dict(decoded)
        value = decoded["value"]
    else:
        metadata = {}
        value = job.source_value
    fingerprint = metadata.get("fingerprint")
    duplicate_decision = metadata.get("duplicate_decision")
    intent = metadata.get("upload_intent")
    marker = (
        intent["marker"]
        if isinstance(intent, dict) and isinstance(intent.get("marker"), str)
        else f"docmind-import:{job.id}"
    )
    last_event_sequence = metadata.get("last_event_sequence", 0)
    if (
        not isinstance(last_event_sequence, int)
        or isinstance(last_event_sequence, bool)
        or last_event_sequence < 0
    ):
        last_event_sequence = 0
    stale_vector_ids = metadata.get("stale_vector_ids", [])
    if not isinstance(stale_vector_ids, list) or not all(
        isinstance(identifier, str) for identifier in stale_vector_ids
    ):
        stale_vector_ids = []
    pending_created_vector_ids = metadata.get("pending_created_vector_ids", [])
    if not isinstance(pending_created_vector_ids, list) or not all(
        isinstance(identifier, str) for identifier in pending_created_vector_ids
    ):
        pending_created_vector_ids = []
    return {
        **metadata,
        "version": 2,
        "value": value,
        "fingerprint": fingerprint if isinstance(fingerprint, str) else "",
        "duplicate_decision": (
            duplicate_decision if isinstance(duplicate_decision, str) else "create"
        ),
        "upload_intent": {"marker": marker},
        "last_event_sequence": last_event_sequence,
        "stale_vector_ids": list(dict.fromkeys(stale_vector_ids)),
        "pending_created_vector_ids": list(
            dict.fromkeys(pending_created_vector_ids)
        ),
    }


def _dump_source_metadata(metadata: dict[str, Any]) -> str:
    return json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))


class ConversationStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_session(self, repository_ids: list[str], *, title: str | None = None) -> SessionRecord:
        with self.database.session() as session:
            record = SessionRecord(title=title, repository_scope_json=json.dumps(repository_ids))
            session.add(record)
            session.flush()
            return record

    def get_session(self, session_id: str) -> SessionRecord | None:
        with self.database.session() as session:
            return session.get(SessionRecord, session_id)

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
            parent = session.get(SessionRecord, message.session_id)
            if parent is not None:
                parent.updated_at = utc_now()
            session.flush()
            return message


class VectorCleanupStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(self, repository_id: str, document_id: str, vector_ids: list[str]) -> None:
        with self.database.session() as session:
            session.add(VectorCleanupRecord(repository_id=repository_id, document_id=document_id, vector_ids_json=json.dumps(vector_ids)))

    def list(self) -> list[VectorCleanupRecord]:
        with self.database.session() as session:
            return list(session.scalars(select(VectorCleanupRecord)))

    def delete(self, cleanup_id: str) -> None:
        with self.database.session() as session:
            record = session.get(VectorCleanupRecord, cleanup_id)
            if record is not None:
                session.delete(record)


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
