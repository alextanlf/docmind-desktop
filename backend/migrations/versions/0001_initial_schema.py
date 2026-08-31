"""initial phase one schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-08-31 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.storage.models import UTCDateTime, utc_timestamp_server_default

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "repositories",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("yuque_id", sa.String(length=255), unique=True),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("yuque_url", sa.Text()),
        sa.Column("sync_status", sa.String(length=64), nullable=False, server_default=sa.text("'unknown'")),
        sa.Column("document_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", UTCDateTime(), nullable=False, server_default=utc_timestamp_server_default()),
        sa.Column("updated_at", UTCDateTime(), nullable=False, server_default=utc_timestamp_server_default()),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(length=36),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("yuque_id", sa.String(length=255)),
        sa.Column("title", sa.String(length=1024), nullable=False),
        sa.Column("source_url", sa.Text()),
        sa.Column("raw_path", sa.Text()),
        sa.Column("markdown_path", sa.Text()),
        sa.Column("source_type", sa.String(length=64), nullable=False, server_default=sa.text("'remote'")),
        sa.Column("content_hash", sa.String(length=128)),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.String(length=64), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("yuque_url", sa.Text()),
        sa.Column("created_at", UTCDateTime(), nullable=False, server_default=utc_timestamp_server_default()),
        sa.Column("updated_at", UTCDateTime(), nullable=False, server_default=utc_timestamp_server_default()),
    )
    op.create_index("ix_documents_repository_id", "documents", ["repository_id"])
    op.create_index("ix_documents_source_url", "documents", ["source_url"])
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "document_id",
            sa.String(length=36),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "repository_id",
            sa.String(length=36),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("section_path", sa.Text()),
        sa.Column("page_number", sa.Integer()),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("source_url", sa.Text()),
        sa.Column("vector_id", sa.String(length=255)),
        sa.Column("created_at", UTCDateTime(), nullable=False, server_default=utc_timestamp_server_default()),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_document_chunks_document_index"),
    )
    op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"])
    op.create_index("ix_document_chunks_repository_id", "document_chunks", ["repository_id"])
    op.create_table(
        "import_jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("source_kind", sa.String(length=64), nullable=False),
        sa.Column("source_value", sa.Text(), nullable=False),
        sa.Column(
            "repository_id",
            sa.String(length=36),
            sa.ForeignKey("repositories.id", ondelete="SET NULL"),
        ),
        sa.Column("state", sa.String(length=32), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("current_stage", sa.String(length=64)),
        sa.Column("progress", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("message", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("error_code", sa.String(length=128)),
        sa.Column("error_message", sa.Text()),
        sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "document_id",
            sa.String(length=36),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
        ),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", UTCDateTime(), nullable=False, server_default=utc_timestamp_server_default()),
        sa.Column("started_at", UTCDateTime()),
        sa.Column("completed_at", UTCDateTime()),
        sa.Column("updated_at", UTCDateTime(), nullable=False, server_default=utc_timestamp_server_default()),
    )
    op.create_index("ix_import_jobs_state", "import_jobs", ["state"])
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("title", sa.String(length=512)),
        sa.Column("repository_scope_json", sa.Text(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("created_at", UTCDateTime(), nullable=False, server_default=utc_timestamp_server_default()),
        sa.Column("updated_at", UTCDateTime(), nullable=False, server_default=utc_timestamp_server_default()),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(length=36),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations_json", sa.Text(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("generation_status", sa.String(length=64), nullable=False, server_default=sa.text("'completed'")),
        sa.Column("created_at", UTCDateTime(), nullable=False, server_default=utc_timestamp_server_default()),
    )
    op.create_index("ix_messages_session_id", "messages", ["session_id"])
    op.create_table(
        "settings",
        sa.Column("key", sa.String(length=255), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False, server_default=utc_timestamp_server_default()),
    )


def downgrade() -> None:
    op.drop_table("settings")
    op.drop_index("ix_messages_session_id", table_name="messages")
    op.drop_table("messages")
    op.drop_table("sessions")
    op.drop_index("ix_import_jobs_state", table_name="import_jobs")
    op.drop_table("import_jobs")
    op.drop_index("ix_document_chunks_repository_id", table_name="document_chunks")
    op.drop_index("ix_document_chunks_document_id", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_index("ix_documents_source_url", table_name="documents")
    op.drop_index("ix_documents_repository_id", table_name="documents")
    op.drop_table("documents")
    op.drop_table("repositories")
