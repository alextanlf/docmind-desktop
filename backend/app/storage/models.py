from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import (
    Boolean,
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
        Enum(
            ImportStatus,
            native_enum=False,
            length=32,
            values_callable=lambda statuses: [status.value for status in statuses],
        ),
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


class SettingRecord(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, server_default=utc_timestamp_server_default()
    )
