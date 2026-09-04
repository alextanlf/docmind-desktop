from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    text,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


class UTCDateTime(TypeDecorator[datetime]):
    """Persist timestamps as UTC ISO-8601 text because SQLite drops tzinfo."""

    impl = String(40)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> str | None:
        del dialect
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware")
        return value.astimezone(UTC).isoformat(timespec="microseconds")

    def process_result_value(self, value: str | datetime | None, dialect: Dialect) -> datetime | None:
        del dialect
        if value is None:
            return None
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def utc_timestamp_server_default():
    return text("(STRFTIME('%Y-%m-%dT%H:%M:%f+00:00', 'now'))")


class Base(DeclarativeBase):
    pass


class ImportStatus(StrEnum):
    PENDING = "pending"
    PARSING = "parsing"
    UPLOADING = "uploading"
    INDEXING = "indexing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BatchState(StrEnum):
    DISCOVERING = "discovering"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BatchItemState(StrEnum):
    DISCOVERED = "discovered"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"

class CrawlEntryState(StrEnum):
    PENDING = "pending"
    FETCHING = "fetching"
    FETCHED = "fetched"
    REJECTED = "rejected"
    FAILED = "failed"

class CrawlEntryRecord(Base):
    __tablename__ = "crawl_entries"
    __table_args__ = (UniqueConstraint("batch_id", "canonical_url", name="uq_crawl_entries_batch_canonical_url"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batch_imports.id", ondelete="CASCADE"), index=True)
    canonical_url: Mapped[str] = mapped_column(Text)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(16), default=CrawlEntryState.PENDING.value, server_default=text("'pending'"))
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    etag: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_modified: Mapped[str | None] = mapped_column(Text, nullable=True)
    cache_ref_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, server_default=utc_timestamp_server_default())
    fetched_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, server_default=utc_timestamp_server_default())


class BatchSourceKind(StrEnum):
    STAGED_DIRECTORY = "staged_directory"
    WEB = "web"
    YUQUE_REPOSITORY = "yuque_repository"
    SEARCH_RESULTS = "search_results"


class BatchItemDecision(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    ATTACH_REMOTE = "attach_remote"
    SKIP = "skip"


def string_enum(enum: type[StrEnum], length: int) -> Enum:
    return Enum(
        enum,
        native_enum=False,
        length=length,
        validate_strings=True,
        values_callable=lambda values: [value.value for value in values],
    )


class RepositoryRecord(Base):
    __tablename__ = "repositories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    yuque_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    yuque_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    sync_status: Mapped[str] = mapped_column(String(64), default="unknown", server_default=text("'unknown'"))
    document_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, server_default=utc_timestamp_server_default()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, server_default=utc_timestamp_server_default()
    )
    documents: Mapped[list[DocumentRecord]] = relationship(passive_deletes=True)
    chunks: Mapped[list[DocumentChunkRecord]] = relationship(passive_deletes=True)


class DocumentRecord(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    yuque_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str] = mapped_column(String(1024))
    source_url: Mapped[str | None] = mapped_column(Text, index=True, nullable=True)
    raw_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    markdown_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(64), default="remote", server_default=text("'remote'"))
    content_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    status: Mapped[str] = mapped_column(String(64), default="pending", server_default=text("'pending'"))
    yuque_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_identity: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_revision: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, server_default=utc_timestamp_server_default()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, server_default=utc_timestamp_server_default()
    )
    chunks: Mapped[list[DocumentChunkRecord]] = relationship(passive_deletes=True)


class DocumentChunkRecord(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (UniqueConstraint("document_id", "chunk_index"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    section_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_count: Mapped[int] = mapped_column(Integer)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    vector_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, server_default=utc_timestamp_server_default()
    )


class ImportJobRecord(Base):
    __tablename__ = "import_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_kind: Mapped[str] = mapped_column(String(64))
    source_value: Mapped[str] = mapped_column(Text)
    repository_id: Mapped[str | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True
    )
    state: Mapped[ImportStatus] = mapped_column(
        string_enum(ImportStatus, 32),
        default=ImportStatus.PENDING,
        server_default=text("'pending'"),
    )
    current_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    progress: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    message: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, server_default=utc_timestamp_server_default()
    )
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, server_default=utc_timestamp_server_default()
    )


class BatchImportRecord(Base):
    __tablename__ = "batch_imports"
    __table_args__ = (
        CheckConstraint(
            "source_kind IN ('staged_directory', 'web', 'yuque_repository', 'search_results')",
            name="ck_batch_imports_source_kind",
        ),
        CheckConstraint(
            "state IN ('discovering', 'awaiting_confirmation', 'running', 'paused', 'completed', "
            "'completed_with_errors', 'failed', 'cancelled')",
            name="ck_batch_imports_state",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_kind: Mapped[BatchSourceKind] = mapped_column(string_enum(BatchSourceKind, 32))
    source_descriptor_json: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
    repository_id: Mapped[str | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    state: Mapped[BatchState] = mapped_column(
        string_enum(BatchState, 32), default=BatchState.DISCOVERING, server_default=text("'discovering'")
    )
    discovery_version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    total_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    selected_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    completed_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    failed_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    skipped_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    progress: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    message: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    last_event_sequence: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, server_default=utc_timestamp_server_default()
    )
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, server_default=utc_timestamp_server_default()
    )


class BatchItemRecord(Base):
    __tablename__ = "batch_items"
    __table_args__ = (
        UniqueConstraint("batch_id", "source_identity", name="uq_batch_items_batch_source_identity"),
        UniqueConstraint("import_job_id", name="uq_batch_items_import_job_id"),
        CheckConstraint(
            "state IN ('discovered', 'queued', 'running', 'completed', 'skipped', 'failed', 'cancelled')",
            name="ck_batch_items_state",
        ),
        CheckConstraint(
            "decision IS NULL OR decision IN ('create', 'update', 'attach_remote', 'skip')",
            name="ck_batch_items_decision",
        ),
        CheckConstraint(
            "decision != 'attach_remote' OR CASE WHEN json_valid(remote_binding_json) THEN CASE WHEN "
            "json_type(remote_binding_json) = 'object' AND (("
            "json_type(remote_binding_json, '$.repository_id') = 'text' AND "
            "length(trim(json_extract(remote_binding_json, '$.repository_id'))) > 0 AND "
            "json_type(remote_binding_json, '$.document_id') = 'text' AND "
            "length(trim(json_extract(remote_binding_json, '$.document_id'))) > 0) OR ("
            "json_type(remote_binding_json, '$.repositoryId') = 'text' AND "
            "length(trim(json_extract(remote_binding_json, '$.repositoryId'))) > 0 AND "
            "json_type(remote_binding_json, '$.documentId') = 'text' AND "
            "length(trim(json_extract(remote_binding_json, '$.documentId'))) > 0)) THEN 1 ELSE 0 END "
            "ELSE 0 END",
            name="ck_batch_items_remote_binding",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batch_imports.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    source_identity: Mapped[str] = mapped_column(Text)
    source_revision: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(1024))
    display_path: Mapped[str] = mapped_column(Text)
    media_type: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(Integer)
    cached_source_json: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
    remote_binding_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    existing_document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    allowed_actions_json: Mapped[str] = mapped_column(Text, default="[]", server_default=text("'[]'"))
    selected: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    decision: Mapped[BatchItemDecision | None] = mapped_column(string_enum(BatchItemDecision, 32), nullable=True)
    state: Mapped[BatchItemState] = mapped_column(
        string_enum(BatchItemState, 32), default=BatchItemState.DISCOVERED, server_default=text("'discovered'")
    )
    import_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("import_jobs.id", ondelete="SET NULL"), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, server_default=utc_timestamp_server_default()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, server_default=utc_timestamp_server_default()
    )


class SessionRecord(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    repository_scope_json: Mapped[str] = mapped_column(Text, default="[]", server_default=text("'[]'"))
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, server_default=utc_timestamp_server_default()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, server_default=utc_timestamp_server_default()
    )
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    summary_due_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

class SessionSummaryRecord(Base):
    __tablename__ = "session_summaries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), unique=True)
    state: Mapped[str] = mapped_column(String(16), default="pending")
    content: Mapped[str | None] = mapped_column(Text)
    topics_json: Mapped[str] = mapped_column(Text, default="[]", server_default=text("'[]'"))
    repository_ids_json: Mapped[str] = mapped_column(Text, default="[]", server_default=text("'[]'"))
    error_code: Mapped[str | None] = mapped_column(String(128))
    retryable: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)

class DistillationRecord(Base):
    __tablename__ = "distillations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str | None] = mapped_column(ForeignKey("sessions.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(512)); content: Mapped[str] = mapped_column(Text)
    key_points_json: Mapped[str] = mapped_column(Text, default="[]", server_default=text("'[]'"))
    repository_ids_json: Mapped[str] = mapped_column(Text, default="[]", server_default=text("'[]'"))
    state: Mapped[str] = mapped_column(String(24), default="draft")
    target: Mapped[str | None] = mapped_column(String(16)); created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now); updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    local_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    remote_document_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    remote_url: Mapped[str | None] = mapped_column(Text, nullable=True)

class MemoryChunkRecord(Base):
    __tablename__ = "memory_chunks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    summary_id: Mapped[str | None] = mapped_column(ForeignKey("session_summaries.id", ondelete="CASCADE"))
    distillation_id: Mapped[str | None] = mapped_column(ForeignKey("distillations.id", ondelete="CASCADE"))
    repository_id: Mapped[str] = mapped_column(String(36)); text: Mapped[str] = mapped_column(Text); chunk_index: Mapped[int] = mapped_column(Integer)

class MemoryVectorCleanupRecord(Base):
    __tablename__ = "memory_vector_cleanups"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    collection: Mapped[str] = mapped_column(String(64)); vector_ids_json: Mapped[str] = mapped_column(Text); created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class ChatRequestRecord(Base):
    """Durable terminal ownership for a streamed chat request."""

    __tablename__ = "chat_requests"

    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    terminal_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    terminal_payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, server_default=utc_timestamp_server_default()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, server_default=utc_timestamp_server_default()
    )


class MessageRecord(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    citations_json: Mapped[str] = mapped_column(Text, default="[]", server_default=text("'[]'"))
    generation_status: Mapped[str] = mapped_column(
        String(64), default="completed", server_default=text("'completed'")
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, server_default=utc_timestamp_server_default()
    )


class VectorCleanupRecord(Base):
    __tablename__ = "vector_cleanups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    repository_id: Mapped[str] = mapped_column(String(36), nullable=False)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    vector_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, server_default=utc_timestamp_server_default())


class DocumentMutationRecord(Base):
    """Durable compensation intent for a document API mutation."""

    __tablename__ = "document_mutations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    repository_id: Mapped[str] = mapped_column(String(36), nullable=False)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, server_default=utc_timestamp_server_default())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, server_default=utc_timestamp_server_default())


class SettingRecord(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, server_default=utc_timestamp_server_default()
    )

class WebSearchRunRecord(Base):
    __tablename__ = "web_search_runs"
    __table_args__ = (UniqueConstraint("request_id", name="uq_web_search_runs_request_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    user_message_id: Mapped[str] = mapped_column(String(36), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="tavily")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, server_default=utc_timestamp_server_default())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

class WebSearchResultRecord(Base):
    __tablename__ = "web_search_results"
    __table_args__ = (UniqueConstraint("run_id", "canonical_url", name="uq_web_search_results_run_url"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("web_search_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    snippet: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, server_default=utc_timestamp_server_default())
