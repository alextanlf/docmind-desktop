from __future__ import annotations

import base64
import inspect
import json
from collections.abc import Callable
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, delete, event, func, or_, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.imports.batch_state_machine import ensure_item_mutable, transition
from app.imports.state_machine import ensure_transition_allowed
from app.schemas.batches import BatchItemPage, BatchItemView, ConfirmBatchInput
from app.storage.database import Database
from app.storage.models import (
    BatchImportRecord,
    BatchItemDecision,
    BatchItemRecord,
    BatchItemState,
    BatchState,
    ChatRequestRecord,
    DocumentChunkRecord,
    DocumentMutationRecord,
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


def _stale_confirmation(message: str = "批次来源或目标已变化，请重新确认") -> DomainError:
    return DomainError("BATCH_STALE_CONFIRMATION", message, 409, False)


_batch_confirmation_session: ContextVar[Session | None] = ContextVar(
    "batch_confirmation_session", default=None
)


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

    def indexed_count_for_repository(self, repository_id: str) -> int:
        with self.database.session() as session:
            statement = select(func.count(func.distinct(DocumentChunkRecord.document_id))).where(
                DocumentChunkRecord.repository_id == repository_id
            )
            return int(session.scalar(statement) or 0)

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

    def find_by_identity(
        self, repository_id: str, source_identity: str
    ) -> DocumentRecord | None:
        with self.database.session() as session:
            statement = select(DocumentRecord).where(
                DocumentRecord.repository_id == repository_id,
                DocumentRecord.source_identity == source_identity,
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
        source_identity: str | None = None,
        source_revision: str | None = None,
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
            if source_identity is not None:
                document.source_identity = source_identity
            if source_revision is not None:
                document.source_revision = source_revision
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

    def owned_vector_ids(self, vector_ids: list[str]) -> set[str]:
        """Return vector IDs still referenced by any active chunk."""
        if not vector_ids:
            return set()
        with self.database.session() as session:
            statement = select(DocumentChunkRecord.id, DocumentChunkRecord.vector_id).where(
                or_(
                    DocumentChunkRecord.vector_id.in_(vector_ids),
                    and_(
                        DocumentChunkRecord.vector_id.is_(None),
                        DocumentChunkRecord.id.in_(vector_ids),
                    ),
                )
            )
            return {vector_id or chunk_id for chunk_id, vector_id in session.execute(statement)}

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

    def restore_snapshot(
        self,
        document: dict[str, Any],
        chunks: list[DocumentChunkRecord],
    ) -> DocumentRecord:
        """Restore document metadata and chunks in one SQLite transaction."""
        with self.database.session() as session:
            record = session.get(DocumentRecord, document["id"])
            if record is None:
                record = DocumentRecord(id=document["id"], repository_id=document["repository_id"], title=document["title"])
                session.add(record)
            for field in (
                "repository_id", "yuque_id", "title", "source_url", "raw_path", "markdown_path",
                "source_type", "content_hash", "chunk_count", "status", "yuque_url", "source_identity",
                "source_revision",
            ):
                if field in document:
                    setattr(record, field, document[field])
            for field in ("created_at", "updated_at"):
                value = document.get(field)
                if isinstance(value, str):
                    value = datetime.fromisoformat(value)
                if value is not None:
                    setattr(record, field, value)
            session.execute(delete(DocumentChunkRecord).where(DocumentChunkRecord.document_id == record.id))
            for chunk in chunks:
                session.add(chunk)
            session.flush()
            return record


class ImportJobStore:
    _INTERRUPTED_STATES = (ImportStatus.PARSING, ImportStatus.UPLOADING, ImportStatus.INDEXING)
    _ACTIVE_STATES = (ImportStatus.PENDING, *_INTERRUPTED_STATES)

    def __init__(self, database: Database) -> None:
        self.database = database

    def create(self, job: ImportJobRecord) -> ImportJobRecord:
        with self.database.session() as session:
            bound = _batch_confirmation_session.get()
            if bound is not None and bound is not session:
                raise DomainError(
                    "BATCH_STATE_CONFLICT",
                    "批次子任务必须在确认事务中保留",
                    409,
                )
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
            bound = _batch_confirmation_session.get()
            if bound is not None and bound is not session:
                raise DomainError(
                    "BATCH_STATE_CONFLICT",
                    "批次子任务必须在确认事务中保留",
                    409,
                )
            return self.reserve_in_session(session, job, fingerprint=fingerprint)

    def reserve_in_session(
        self,
        session: Session,
        job: ImportJobRecord,
        *,
        fingerprint: str,
    ) -> ImportJobRecord:
        statement = select(ImportJobRecord).where(
            ImportJobRecord.repository_id == job.repository_id,
            ImportJobRecord.state.in_(self._ACTIVE_STATES),
        )
        for active in session.scalars(statement):
            if _source_fingerprint(active.source_value) == fingerprint:
                raise DomainError("IMPORT_ALREADY_RUNNING", "导入任务正在运行", 409)
        session.add(job)
        session.flush()
        session.info.setdefault("batch_reserved_job_ids", set()).add(job.id)
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
                metadata = _normalized_source_metadata(job)
                if job.cancel_requested and metadata.get("coherence_pending") is not True:
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


class BatchImportStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_batch(self, batch: BatchImportRecord) -> BatchImportRecord:
        batch.source_descriptor_json = _source_descriptor_json(batch.source_descriptor_json)
        with self.database.session() as session:
            session.add(batch)
            session.flush()
            return batch

    def get(self, batch_id: str) -> BatchImportRecord | None:
        with self.database.session() as session:
            return session.get(BatchImportRecord, batch_id)

    def get_item(self, item_id: str) -> BatchItemRecord | None:
        with self.database.session() as session:
            return session.get(BatchItemRecord, item_id)

    def list_item_records(self, batch_id: str) -> list[BatchItemRecord]:
        with self.database.session() as session:
            return list(
                session.scalars(
                    select(BatchItemRecord)
                    .where(BatchItemRecord.batch_id == batch_id)
                    .order_by(BatchItemRecord.ordinal, BatchItemRecord.id)
                )
            )

    def list_items(
        self,
        batch_id: str,
        cursor: str | None = None,
        state: BatchItemState | str | None = None,
        selected: bool | None = None,
        *,
        limit: int = 100,
    ) -> BatchItemPage:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        after = _decode_batch_item_cursor(cursor) if cursor is not None else None
        with self.database.session() as session:
            conditions = [BatchItemRecord.batch_id == batch_id]
            if state is not None:
                conditions.append(BatchItemRecord.state == BatchItemState(state))
            if selected is not None:
                conditions.append(BatchItemRecord.selected == selected)
            if after is not None:
                ordinal, item_id = after
                conditions.append(
                    or_(
                        BatchItemRecord.ordinal > ordinal,
                        and_(BatchItemRecord.ordinal == ordinal, BatchItemRecord.id > item_id),
                    )
                )
            statement = (
                select(BatchItemRecord)
                .where(*conditions)
                .order_by(BatchItemRecord.ordinal, BatchItemRecord.id)
                .limit(limit + 1)
            )
            records = list(session.scalars(statement))
            has_more = len(records) > limit
            page_records = records[:limit]
            next_cursor = _encode_batch_item_cursor(page_records[-1]) if has_more else None
            return BatchItemPage(
                items=[_batch_item_view(record) for record in page_records], next_cursor=next_cursor
            )

    def insert_discovered_items(
        self, batch_id: str, items: list[BatchItemRecord]
    ) -> list[BatchItemRecord]:
        with self.database.session() as session:
            batch = session.get(BatchImportRecord, batch_id)
            if batch is None:
                raise DomainError("BATCH_NOT_FOUND", "批次不存在", 404)
            max_ordinal = session.scalar(
                select(func.max(BatchItemRecord.ordinal)).where(BatchItemRecord.batch_id == batch_id)
            )
            next_ordinal = (int(max_ordinal) if max_ordinal is not None else -1) + 1
            for offset, item in enumerate(items):
                item.batch_id = batch_id
                item.ordinal = next_ordinal + offset
                item.cached_source_json = _cached_source_json(item.cached_source_json)
                item.allowed_actions_json = _allowed_actions_json(item.allowed_actions_json)
                session.add(item)
            batch.total_count += len(items)
            batch.updated_at = utc_now()
            session.flush()
            return items

    def set_confirmation(self, batch_id: str, confirmation: ConfirmBatchInput) -> BatchImportRecord:
        decisions = {str(item.item_id): BatchItemDecision(item.decision) for item in confirmation.items}
        if len(decisions) != len(confirmation.items):
            raise DomainError("BATCH_CONFIRMATION_INVALID", "确认项重复", 422)
        with self.database.session() as session:
            batch = session.get(BatchImportRecord, batch_id)
            if batch is None:
                raise DomainError("BATCH_NOT_FOUND", "批次不存在", 404)
            if batch.discovery_version != confirmation.discovery_version:
                raise _stale_confirmation("发现结果已更新，请重新确认")
            if batch.state != BatchState.AWAITING_CONFIRMATION:
                raise DomainError("BATCH_STATE_CONFLICT", "批次当前不可确认", 409)
            items = list(
                session.scalars(
                    select(BatchItemRecord)
                    .where(BatchItemRecord.batch_id == batch_id)
                    .order_by(BatchItemRecord.ordinal)
                )
            )
            item_ids = {item.id for item in items}
            if not decisions.keys() <= item_ids:
                raise DomainError("BATCH_CONFIRMATION_INVALID", "确认项不属于该批次", 422)
            for item in items:
                ensure_item_mutable(item.state)
                decision = decisions.get(item.id)
                allowed_actions = _decode_allowed_actions(item.allowed_actions_json)
                if decision is not None and decision.value not in allowed_actions:
                    raise DomainError("BATCH_CONFIRMATION_INVALID", "批次项不允许该确认操作", 422)
                if decision == BatchItemDecision.ATTACH_REMOTE and not _has_remote_binding(
                    item.remote_binding_json
                ):
                    raise DomainError("BATCH_CONFIRMATION_INVALID", "attach_remote 需要远端绑定", 422)
                # Preserve a previously reserved child when a confirmation
                # request is retried after a partial transaction.
                if item.import_job_id is None:
                    item.decision = decision
                    item.selected = decision not in {None, BatchItemDecision.SKIP}
                    item.state = BatchItemState.SKIPPED if decision == BatchItemDecision.SKIP else BatchItemState.DISCOVERED
                elif decision != item.decision:
                    raise DomainError("BATCH_CONFIRMATION_INVALID", "已保留的批次项决策不可修改", 422)
                item.updated_at = utc_now()
            batch.selected_count = sum(item.selected for item in items)
            batch.skipped_count = sum(item.state == BatchItemState.SKIPPED for item in items)
            batch.updated_at = utc_now()
            session.flush()
            return batch

    def confirm_and_reserve(
        self,
        batch_id: str,
        confirmation: ConfirmBatchInput,
        *,
        reserve_child: Callable[..., str],
        reserve_children: Callable[..., dict[str, str]] | None = None,
        validate_sources: Callable[..., Any] | None = None,
    ) -> BatchImportRecord:
        """Confirm a discovery snapshot and bind all children in one write transaction."""
        decisions = {
            str(item.item_id): BatchItemDecision(item.decision)
            for item in confirmation.items
        }
        if len(decisions) != len(confirmation.items):
            raise DomainError("BATCH_CONFIRMATION_INVALID", "确认项重复", 422)
        with self.database.session() as session, self.database.bind_confirmation_session(session):
            token = _batch_confirmation_session.set(session)
            try:
                return self._confirm_and_reserve_in_session(
                    session,
                    batch_id,
                    confirmation,
                    reserve_child=reserve_child,
                    reserve_children=reserve_children,
                    validate_sources=validate_sources,
                )
            finally:
                _batch_confirmation_session.reset(token)

    def _confirm_and_reserve_in_session(
        self,
        session: Session,
        batch_id: str,
        confirmation: ConfirmBatchInput,
        *,
        reserve_child: Callable[..., str],
        reserve_children: Callable[..., dict[str, str]] | None,
        validate_sources: Callable[..., Any] | None,
    ) -> BatchImportRecord:
        if self.database.url.startswith("sqlite"):
            session.execute(text("BEGIN IMMEDIATE"))
        decisions = {
            str(item.item_id): BatchItemDecision(item.decision)
            for item in confirmation.items
        }
        batch = session.get(BatchImportRecord, batch_id)
        if batch is None:
            raise DomainError("BATCH_NOT_FOUND", "批次不存在", 404)
        if batch.state != BatchState.AWAITING_CONFIRMATION:
            raise DomainError("BATCH_STATE_CONFLICT", "批次当前不可确认", 409)
        if batch.discovery_version != confirmation.discovery_version:
            raise _stale_confirmation("发现结果已更新，请重新确认")
        repository = (
            session.get(RepositoryRecord, batch.repository_id)
            if batch.repository_id is not None
            else None
        )
        if repository is None or not repository.yuque_id:
            raise _stale_confirmation("目标知识库已变化，请重新确认")
        items = list(
            session.scalars(
                select(BatchItemRecord)
                .where(BatchItemRecord.batch_id == batch_id)
                .order_by(BatchItemRecord.ordinal, BatchItemRecord.id)
            )
        )
        item_ids = {item.id for item in items}
        if not decisions.keys() <= item_ids:
            raise DomainError("BATCH_CONFIRMATION_INVALID", "确认项不属于该批次", 422)

        targets: dict[str, DocumentRecord | None] = {}
        selected_items: list[BatchItemRecord] = []
        for item in items:
            ensure_item_mutable(item.state)
            if item.state != BatchItemState.DISCOVERED or item.import_job_id is not None:
                raise DomainError("BATCH_STATE_CONFLICT", "批次项当前不可确认", 409)
            decision = decisions.get(item.id)
            if decision is None:
                continue
            target = self._current_document_target(
                session,
                batch.repository_id or "",
                item,
            )
            current_actions = self._current_actions(item, target)
            persisted_actions = set(_decode_allowed_actions(item.allowed_actions_json))
            if (
                persisted_actions != current_actions
                or item.existing_document_id != (target.id if target is not None else None)
                or decision.value not in current_actions
                or (
                    decision == BatchItemDecision.ATTACH_REMOTE
                    and not _has_remote_binding(item.remote_binding_json)
                )
            ):
                raise _stale_confirmation()
            targets[item.id] = target
            if decision != BatchItemDecision.SKIP:
                selected_items.append(item)

        if validate_sources is not None and selected_items:
            try:
                validation = validate_sources(
                    selected_items,
                    batch=batch,
                    repository_id=batch.repository_id,
                    session=session,
                )
            except DomainError as error:
                if error.code in {"BATCH_SOURCE_CHANGED", "BATCH_STALE_CONFIRMATION"}:
                    raise _stale_confirmation(error.message) from None
                raise
            if inspect.isawaitable(validation):
                raise TypeError("batch source validation must be synchronous inside the transaction")

        for item in items:
            decision = decisions.get(item.id)
            item.decision = decision
            item.selected = decision not in {None, BatchItemDecision.SKIP}
            if decision is not None:
                target = targets[item.id]
                item.existing_document_id = target.id if target is not None else None

        job_ids: Any = None
        reserved_marker = session.info.setdefault("batch_reserved_job_ids", set())
        existing_job_ids = set(session.scalars(select(ImportJobRecord.id)))
        existing_job_ids.update(
            obj.id
            for obj in session.new
            if isinstance(obj, ImportJobRecord) and isinstance(obj.id, str)
        )
        created_job_ids: set[str] = set()

        def track_new_jobs(current_session: Session, *_: Any) -> None:
            created_job_ids.update(
                obj.id
                for obj in current_session.new
                if isinstance(obj, ImportJobRecord)
                and obj.id not in existing_job_ids
            )

        event.listen(session, "after_flush", track_new_jobs)
        try:
            if reserve_children is not None and selected_items:
                job_ids = reserve_children(
                    selected_items,
                    batch=batch,
                    repository_id=batch.repository_id,
                    session=session,
                )
            else:
                job_ids = {
                    item.id: reserve_child(
                        item,
                        batch=batch,
                        repository_id=batch.repository_id,
                        session=session,
                    )
                    for item in selected_items
                }
            if inspect.isawaitable(job_ids):
                raise TypeError("batch reservation must be synchronous inside the transaction")
            if not isinstance(job_ids, dict) or set(job_ids) != {item.id for item in selected_items}:
                raise RuntimeError("batch reservation returned invalid child mapping")
            # Flush once more so callbacks that only add a child (without
            # calling ``flush`` themselves) are tracked by the listener.
            session.flush()
            allowed_job_ids = reserved_marker | created_job_ids
            returned_job_ids = [
                value for value in job_ids.values() if isinstance(value, str)
            ]
            if len(returned_job_ids) != len(set(returned_job_ids)):
                raise RuntimeError("batch reservation returned duplicate child jobs")
            if created_job_ids - set(returned_job_ids):
                raise RuntimeError("batch reservation created unmapped child jobs")
            for item in selected_items:
                job_id = job_ids[item.id]
                if not isinstance(job_id, str) or (
                    job_id not in allowed_job_ids
                ):
                    raise RuntimeError("batch reservation must use the confirmation session")
                if job_id in existing_job_ids:
                    raise RuntimeError("batch reservation returned an existing child job")
                job = session.get(ImportJobRecord, job_id)
                if job is None or job.repository_id != batch.repository_id:
                    raise RuntimeError("batch reservation returned invalid child job")
                expected_fingerprint = item.source_revision.removeprefix("sha256:")
                actual_fingerprint = _source_fingerprint(job.source_value)
                if len(expected_fingerprint) == 64 and actual_fingerprint != expected_fingerprint:
                    raise RuntimeError("batch reservation returned mismatched child fingerprint")
        except DomainError as error:
            if created_job_ids:
                session.execute(delete(ImportJobRecord).where(ImportJobRecord.id.in_(created_job_ids)))
            if error.code in {"BATCH_SOURCE_CHANGED", "BATCH_STALE_CONFIRMATION"}:
                raise _stale_confirmation(error.message) from None
            raise
        except (TypeError, RuntimeError):
            if created_job_ids:
                session.execute(delete(ImportJobRecord).where(ImportJobRecord.id.in_(created_job_ids)))
            raise
        finally:
            event.remove(session, "after_flush", track_new_jobs)

        now = utc_now()
        for item in items:
            decision = decisions.get(item.id)
            item.decision = decision
            item.selected = decision not in {None, BatchItemDecision.SKIP}
            item.existing_document_id = (
                targets[item.id].id
                if decision is not None and targets[item.id] is not None
                else None
            )
            if decision == BatchItemDecision.SKIP:
                item.state = BatchItemState.SKIPPED
            elif decision is not None:
                job_id = job_ids.get(item.id)
                if not isinstance(job_id, str):
                    raise RuntimeError("batch reservation did not return a child job id")
                session.flush()
                if session.get(ImportJobRecord, job_id) is None:
                    raise RuntimeError("batch reservation did not persist its child job")
                item.import_job_id = job_id
                item.state = BatchItemState.QUEUED
            item.updated_at = now

        batch.selected_count = sum(item.selected for item in items)
        batch.skipped_count = sum(
            item.state == BatchItemState.SKIPPED for item in items
        )
        target_state = (
            BatchState.RUNNING
            if batch.selected_count
            else BatchState.COMPLETED
        )
        transition(batch.state, target_state)
        batch.state = target_state
        batch.message = (
            "批次导入中" if target_state == BatchState.RUNNING else "批次无待导入项"
        )
        if target_state == BatchState.RUNNING:
            batch.started_at = now
        else:
            batch.completed_at = now
            batch.progress = 100
        batch.updated_at = now
        session.flush()
        return batch

    @staticmethod
    def _current_document_target(
        session: Session,
        repository_id: str,
        item: BatchItemRecord,
    ) -> DocumentRecord | None:
        by_identity = session.scalar(
            select(DocumentRecord).where(
                DocumentRecord.repository_id == repository_id,
                DocumentRecord.source_identity == item.source_identity,
            )
        )
        if by_identity is not None:
            return by_identity
        revision = item.source_revision.removeprefix("sha256:")
        if len(revision) != 64:
            return None
        return session.scalar(
            select(DocumentRecord).where(
                DocumentRecord.repository_id == repository_id,
                DocumentRecord.content_hash == revision,
            )
        )

    @staticmethod
    def _current_actions(
        item: BatchItemRecord,
        target: DocumentRecord | None,
    ) -> set[str]:
        if target is not None:
            return {BatchItemDecision.UPDATE.value, BatchItemDecision.SKIP.value}
        if _has_remote_binding(item.remote_binding_json):
            return {
                BatchItemDecision.ATTACH_REMOTE.value,
                BatchItemDecision.SKIP.value,
            }
        return {BatchItemDecision.CREATE.value, BatchItemDecision.SKIP.value}

    def reserve_child_job(self, item_id: str, import_job_id: str) -> BatchItemRecord:
        with self.database.session() as session:
            item = session.get(BatchItemRecord, item_id)
            if item is None:
                raise DomainError("BATCH_ITEM_NOT_FOUND", "批次项不存在", 404)
            ensure_item_mutable(item.state)
            if item.import_job_id is not None:
                raise DomainError("BATCH_ITEM_ALREADY_RESERVED", "批次项已绑定子任务", 409)
            if not item.selected or item.decision is None:
                raise DomainError("BATCH_STATE_CONFLICT", "批次项尚未确认", 409)
            if session.get(ImportJobRecord, import_job_id) is None:
                raise DomainError(
                    "BATCH_ITEM_JOB_NOT_FOUND", "子任务不存在", 409
                )
            item.import_job_id = import_job_id
            item.state = BatchItemState.QUEUED
            item.updated_at = utc_now()
            session.flush()
            return item

    def set_state(self, batch_id: str, target: BatchState, *, message: str | None = None) -> BatchImportRecord:
        with self.database.session() as session:
            batch = session.get(BatchImportRecord, batch_id)
            if batch is None:
                raise DomainError("BATCH_NOT_FOUND", "批次不存在", 404)
            if batch.state != target:
                transition(batch.state, target)
                batch.state = target
                if target == BatchState.RUNNING and batch.started_at is None:
                    batch.started_at = utc_now()
                if target in {
                    BatchState.COMPLETED,
                    BatchState.COMPLETED_WITH_ERRORS,
                    BatchState.FAILED,
                    BatchState.CANCELLED,
                }:
                    batch.completed_at = utc_now()
            if message is not None:
                batch.message = message
            batch.updated_at = utc_now()
            session.flush()
            return batch

    def request_cancel(self, batch_id: str) -> BatchImportRecord:
        with self.database.session() as session:
            batch = session.get(BatchImportRecord, batch_id)
            if batch is None:
                raise DomainError("BATCH_NOT_FOUND", "批次不存在", 404)
            if batch.state in {
                BatchState.COMPLETED,
                BatchState.COMPLETED_WITH_ERRORS,
                BatchState.FAILED,
                BatchState.CANCELLED,
            }:
                return batch
            batch.cancel_requested = True
            batch.updated_at = utc_now()
            session.flush()
            return batch

    def mark_item_running(self, item_id: str) -> BatchItemRecord:
        with self.database.session() as session:
            item = session.get(BatchItemRecord, item_id)
            if item is None:
                raise DomainError("BATCH_ITEM_NOT_FOUND", "批次项不存在", 404)
            if item.state == BatchItemState.RUNNING:
                return item
            ensure_item_mutable(item.state)
            if item.state != BatchItemState.QUEUED:
                raise DomainError("BATCH_STATE_CONFLICT", "批次项未排队", 409)
            item.state = BatchItemState.RUNNING
            item.updated_at = utc_now()
            session.flush()
            return item

    def mark_item_cancelled(self, item_id: str) -> BatchItemRecord:
        with self.database.session() as session:
            item = session.get(BatchItemRecord, item_id)
            if item is None:
                raise DomainError("BATCH_ITEM_NOT_FOUND", "批次项不存在", 404)
            if item.state not in {
                BatchItemState.DISCOVERED,
                BatchItemState.QUEUED,
                BatchItemState.RUNNING,
            }:
                return item
            item.state = BatchItemState.CANCELLED
            item.updated_at = utc_now()
            session.flush()
            return item

    def finish_cancel(self, batch_id: str) -> BatchImportRecord:
        """Make selected item cancellation terminal before the parent transition."""
        with self.database.session() as session:
            batch = session.get(BatchImportRecord, batch_id)
            if batch is None:
                raise DomainError("BATCH_NOT_FOUND", "批次不存在", 404)
            if batch.state in {
                BatchState.COMPLETED,
                BatchState.COMPLETED_WITH_ERRORS,
                BatchState.FAILED,
                BatchState.CANCELLED,
            }:
                return batch
            now = utc_now()
            items = list(
                session.scalars(
                    select(BatchItemRecord).where(BatchItemRecord.batch_id == batch_id)
                )
            )
            for item in items:
                if item.selected and item.state in {
                    BatchItemState.DISCOVERED,
                    BatchItemState.QUEUED,
                    BatchItemState.RUNNING,
                }:
                    item.state = BatchItemState.CANCELLED
                    item.updated_at = now
            transition(batch.state, BatchState.CANCELLED)
            batch.state = BatchState.CANCELLED
            batch.completed_at = now
            batch.total_count = len(items)
            batch.selected_count = sum(item.selected for item in items)
            batch.completed_count = sum(item.state == BatchItemState.COMPLETED for item in items)
            batch.failed_count = sum(item.state == BatchItemState.FAILED for item in items)
            batch.skipped_count = sum(item.state == BatchItemState.SKIPPED for item in items)
            finished = sum(item.state in {BatchItemState.COMPLETED, BatchItemState.FAILED, BatchItemState.SKIPPED, BatchItemState.CANCELLED} for item in items)
            batch.progress = int(finished * 100 / len(items)) if items else 100
            batch.message = "批次已取消"
            batch.updated_at = now
            session.flush()
            return batch

    def mark_failed(
        self, item_id: str, *, code: str, message: str, retryable: bool
    ) -> BatchItemRecord:
        with self.database.session() as session:
            item = session.get(BatchItemRecord, item_id)
            if item is None:
                raise DomainError("BATCH_ITEM_NOT_FOUND", "批次项不存在", 404)
            if item.state in {BatchItemState.COMPLETED, BatchItemState.SKIPPED, BatchItemState.CANCELLED}:
                return item
            item.state = BatchItemState.FAILED
            item.error_code = code
            item.error_message = message
            item.retryable = retryable
            item.updated_at = utc_now()
            session.flush()
            return item

    def mark_item_queued(self, item_id: str) -> BatchItemRecord:
        with self.database.session() as session:
            item = session.get(BatchItemRecord, item_id)
            if item is None:
                raise DomainError("BATCH_ITEM_NOT_FOUND", "批次项不存在", 404)
            if item.state in {BatchItemState.COMPLETED, BatchItemState.SKIPPED, BatchItemState.CANCELLED}:
                raise DomainError("BATCH_STATE_CONFLICT", "批次项已完成，无法重试", 409)
            item.state = BatchItemState.QUEUED
            item.error_code = None
            item.error_message = None
            item.retryable = False
            item.updated_at = utc_now()
            session.flush()
            return item

    def sync_child_terminal(self, item_id: str, child: ImportJobRecord | Any) -> BatchItemRecord:
        state_value = getattr(child, "state", None)
        if isinstance(state_value, str):
            state_value = ImportStatus(state_value)
        with self.database.session() as session:
            item = session.get(BatchItemRecord, item_id)
            if item is None:
                raise DomainError("BATCH_ITEM_NOT_FOUND", "批次项不存在", 404)
            if item.state in {BatchItemState.COMPLETED, BatchItemState.SKIPPED, BatchItemState.CANCELLED}:
                return item
            if state_value == ImportStatus.COMPLETED:
                item.state = BatchItemState.COMPLETED
            elif state_value == ImportStatus.CANCELLED:
                item.state = BatchItemState.CANCELLED
            elif state_value == ImportStatus.FAILED:
                item.state = BatchItemState.FAILED
                item.error_code = getattr(child, "error_code", None)
                item.error_message = getattr(child, "error_message", None)
                item.retryable = bool(getattr(child, "retryable", False))
            else:
                return item
            item.updated_at = utc_now()
            session.flush()
            return item

    def update_counts(
        self,
        batch_id: str,
        *,
        total_count: int | None = None,
        selected_count: int | None = None,
        completed_count: int | None = None,
        failed_count: int | None = None,
        skipped_count: int | None = None,
        progress: int | None = None,
        message: str | None = None,
    ) -> BatchImportRecord:
        values = {
            "total_count": total_count,
            "selected_count": selected_count,
            "completed_count": completed_count,
            "failed_count": failed_count,
            "skipped_count": skipped_count,
            "progress": progress,
            "message": message,
        }
        if any(value is not None and isinstance(value, int) and value < 0 for value in values.values()):
            raise ValueError("batch counts and progress must be non-negative")
        if progress is not None and progress > 100:
            raise ValueError("progress must be at most 100")
        with self.database.session() as session:
            batch = session.get(BatchImportRecord, batch_id)
            if batch is None:
                raise DomainError("BATCH_NOT_FOUND", "批次不存在", 404)
            for field, value in values.items():
                if value is not None:
                    setattr(batch, field, value)
            batch.updated_at = utc_now()
            session.flush()
            return batch

    def allocate_event_sequence(self, batch_id: str) -> int:
        with self.database.session() as session:
            sequence = session.scalar(
                update(BatchImportRecord)
                .where(BatchImportRecord.id == batch_id)
                .values(
                    last_event_sequence=BatchImportRecord.last_event_sequence + 1,
                    updated_at=utc_now(),
                )
                .returning(BatchImportRecord.last_event_sequence)
            )
            if sequence is None:
                raise DomainError("BATCH_NOT_FOUND", "批次不存在", 404)
            return int(sequence)

    def recover_on_startup(self) -> int:
        with self.database.session() as session:
            batches = list(session.scalars(select(BatchImportRecord)))
            now = utc_now()
            recovered = 0
            for batch in batches:
                if batch.state == BatchState.DISCOVERING:
                    batch.state = BatchState.FAILED
                    batch.error_code = "BATCH_APP_RESTARTED"
                    batch.error_message = "应用重启导致目录发现中断"
                    batch.retryable = True
                    batch.message = "应用重启导致目录发现中断"
                    batch.completed_at = now
                    batch.updated_at = now
                    recovered += 1
                elif batch.state == BatchState.RUNNING:
                    batch.state = BatchState.PAUSED
                    batch.message = "应用重启导致批次暂停"
                    batch.updated_at = now
                    recovered += 1
                if batch.state == BatchState.PAUSED:
                    active_items = session.scalars(
                        select(BatchItemRecord).where(
                            BatchItemRecord.batch_id == batch.id,
                            BatchItemRecord.state == BatchItemState.RUNNING,
                        )
                    )
                    for item in active_items:
                        item.state = BatchItemState.QUEUED
                        item.updated_at = now
            return recovered


def _source_descriptor_json(value: Any) -> str:
    return _encode_json(value, expected=dict, default={})


def _cached_source_json(value: Any) -> str:
    return _encode_json(value, expected=dict, default={})


def _allowed_actions_json(value: Any) -> str:
    return _encode_json(value, expected=list, default=[])


def _encode_json(value: Any, *, expected: type, default: Any) -> str:
    decoded = default if value is None else value
    if isinstance(decoded, str):
        try:
            decoded = json.loads(decoded)
        except json.JSONDecodeError as error:
            raise ValueError("batch JSON must be valid") from error
    if not isinstance(decoded, expected):
        raise TypeError(f"batch JSON must encode a {expected.__name__}")
    return json.dumps(decoded, ensure_ascii=False, separators=(",", ":"))


def _batch_item_view(item: BatchItemRecord) -> BatchItemView:
    allowed_actions = _decode_allowed_actions(item.allowed_actions_json)
    return BatchItemView(
        id=item.id,
        batch_id=item.batch_id,
        ordinal=item.ordinal,
        title=item.title,
        display_path=item.display_path,
        media_type=item.media_type,
        size_bytes=item.size_bytes,
        source_revision=item.source_revision,
        allowed_actions=allowed_actions,
        selected=item.selected,
        decision=item.decision,
        state=item.state,
        import_job_id=item.import_job_id,
        error_code=item.error_code,
        error_message=item.error_message,
        retryable=item.retryable,
    )


def _decode_allowed_actions(value: str) -> list[str]:
    allowed_actions = json.loads(value)
    if not isinstance(allowed_actions, list) or not all(isinstance(action, str) for action in allowed_actions):
        raise ValueError("allowed_actions_json must encode a list of strings")
    return allowed_actions


def _has_remote_binding(value: str | None) -> bool:
    if value is None:
        return False
    try:
        binding = json.loads(value)
    except json.JSONDecodeError:
        return False
    if not isinstance(binding, dict):
        return False
    repository_id = binding.get("repository_id", binding.get("repositoryId"))
    document_id = binding.get("document_id", binding.get("documentId"))
    return (
        isinstance(repository_id, str)
        and bool(repository_id.strip())
        and isinstance(document_id, str)
        and bool(document_id.strip())
    )


def _encode_batch_item_cursor(item: BatchItemRecord) -> str:
    value = f"{item.ordinal}:{item.id}".encode()
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode_batch_item_cursor(cursor: str) -> tuple[int, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        ordinal_text, item_id = base64.urlsafe_b64decode(padded.encode()).decode().split(":", 1)
        return int(ordinal_text), item_id
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("invalid batch item cursor") from error


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

    def claim_chat_request(
        self, request_id: str, session_id: str
    ) -> tuple[ChatRequestRecord, bool]:
        """Atomically reserve a chat request ID for exactly one producer."""
        with self.database.session() as session:
            result = session.execute(
                sqlite_insert(ChatRequestRecord)
                .values(request_id=request_id, session_id=session_id)
                .on_conflict_do_nothing(index_elements=["request_id"])
            )
            record = session.get(ChatRequestRecord, request_id)
            if record is None:
                raise DomainError("CHAT_REQUEST_CONFLICT", "聊天请求状态冲突", 409)
            return record, result.rowcount == 1

    def complete_chat_request(
        self, request_id: str, terminal_type: str, payload: dict[str, Any]
    ) -> ChatRequestRecord:
        with self.database.session() as session:
            record = session.get(ChatRequestRecord, request_id)
            if record is None:
                raise DomainError("CHAT_REQUEST_CONFLICT", "聊天请求状态冲突", 409)
            record.terminal_type = terminal_type
            record.terminal_payload_json = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            )
            record.updated_at = utc_now()
            session.flush()
            return record


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


class DocumentMutationStore:
    """Small durable store for API mutation compensation intents."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def create(
        self,
        *,
        mutation_id: str | None = None,
        operation: str,
        repository_id: str,
        document_id: str | None,
        payload: dict[str, Any],
    ) -> DocumentMutationRecord:
        with self.database.session() as session:
            record = DocumentMutationRecord(
                id=mutation_id or str(uuid4()),
                operation=operation,
                repository_id=repository_id,
                document_id=document_id,
                payload_json=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            )
            session.add(record)
            session.flush()
            return record

    def list(self) -> list[DocumentMutationRecord]:
        with self.database.session() as session:
            return list(
                session.scalars(
                    select(DocumentMutationRecord).order_by(DocumentMutationRecord.created_at)
                )
            )

    def get(self, mutation_id: str) -> DocumentMutationRecord | None:
        with self.database.session() as session:
            return session.get(DocumentMutationRecord, mutation_id)

    def update(
        self,
        mutation_id: str,
        *,
        document_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> DocumentMutationRecord:
        with self.database.session() as session:
            record = session.get(DocumentMutationRecord, mutation_id)
            if record is None:
                raise DomainError("MUTATION_NOT_FOUND", "文档变更意图不存在", 409)
            if document_id is not None:
                record.document_id = document_id
            if payload is not None:
                record.payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            record.updated_at = utc_now()
            session.flush()
            return record

    def delete(self, mutation_id: str) -> None:
        with self.database.session() as session:
            record = session.get(DocumentMutationRecord, mutation_id)
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
        self.set_many({key: value})

    def set_many(self, values: dict[str, str]) -> None:
        with self.database.session() as session:
            for key, value in values.items():
                record = session.get(SettingRecord, key)
                if record is None:
                    session.add(SettingRecord(key=key, value=value))
                else:
                    record.value = value
                    record.updated_at = utc_now()
