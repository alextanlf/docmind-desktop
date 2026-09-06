"""phase two batch storage core

Revision ID: 0005_phase2_batch_core
Revises: 0004_chat_request_idempotency
Create Date: 2026-09-02 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.storage.models import UTCDateTime, utc_timestamp_server_default

revision = "0005_phase2_batch_core"
down_revision = "0004_chat_request_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("source_identity", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("source_revision", sa.Text(), nullable=True))
    op.create_index(
        "uq_documents_repository_source_identity",
        "documents",
        ["repository_id", "source_identity"],
        unique=True,
        sqlite_where=sa.text("source_identity IS NOT NULL"),
    )
    op.create_table(
        "batch_imports",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("source_descriptor_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "repository_id",
            sa.String(length=36),
            sa.ForeignKey("repositories.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("state", sa.String(length=32), nullable=False, server_default=sa.text("'discovering'")),
        sa.Column("discovery_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("total_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("selected_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("completed_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("skipped_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("progress", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("message", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_event_sequence", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", UTCDateTime(), nullable=False, server_default=utc_timestamp_server_default()),
        sa.Column("started_at", UTCDateTime(), nullable=True),
        sa.Column("completed_at", UTCDateTime(), nullable=True),
        sa.Column("updated_at", UTCDateTime(), nullable=False, server_default=utc_timestamp_server_default()),
        sa.CheckConstraint(
            "source_kind IN ('staged_directory', 'web', 'yuque_repository', 'search_results')",
            name="ck_batch_imports_source_kind",
        ),
        sa.CheckConstraint(
            "state IN ('discovering', 'awaiting_confirmation', 'running', 'paused', 'completed', "
            "'completed_with_errors', 'failed', 'cancelled')",
            name="ck_batch_imports_state",
        ),
    )
    op.create_index("ix_batch_imports_repository_id", "batch_imports", ["repository_id"])
    op.create_index("ix_batch_imports_state", "batch_imports", ["state"])
    op.create_table(
        "batch_items",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "batch_id",
            sa.String(length=36),
            sa.ForeignKey("batch_imports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("source_identity", sa.Text(), nullable=False),
        sa.Column("source_revision", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=1024), nullable=False),
        sa.Column("display_path", sa.Text(), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("cached_source_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("remote_binding_json", sa.Text(), nullable=True),
        sa.Column(
            "existing_document_id",
            sa.String(length=36),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("allowed_actions_json", sa.Text(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("selected", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("decision", sa.String(length=32), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False, server_default=sa.text("'discovered'")),
        sa.Column(
            "import_job_id",
            sa.String(length=36),
            sa.ForeignKey("import_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", UTCDateTime(), nullable=False, server_default=utc_timestamp_server_default()),
        sa.Column("updated_at", UTCDateTime(), nullable=False, server_default=utc_timestamp_server_default()),
        sa.UniqueConstraint("batch_id", "source_identity", name="uq_batch_items_batch_source_identity"),
        sa.UniqueConstraint("import_job_id", name="uq_batch_items_import_job_id"),
        sa.CheckConstraint(
            "state IN ('discovered', 'queued', 'running', 'completed', 'skipped', 'failed', 'cancelled')",
            name="ck_batch_items_state",
        ),
        sa.CheckConstraint(
            "decision IS NULL OR decision IN ('create', 'update', 'attach_remote', 'skip')",
            name="ck_batch_items_decision",
        ),
        sa.CheckConstraint(
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
    op.create_index("ix_batch_items_batch_id", "batch_items", ["batch_id"])
    op.create_index("ix_batch_items_existing_document_id", "batch_items", ["existing_document_id"])


def downgrade() -> None:
    op.drop_index("ix_batch_items_existing_document_id", table_name="batch_items")
    op.drop_index("ix_batch_items_batch_id", table_name="batch_items")
    op.drop_table("batch_items")
    op.drop_index("ix_batch_imports_state", table_name="batch_imports")
    op.drop_index("ix_batch_imports_repository_id", table_name="batch_imports")
    op.drop_table("batch_imports")
    op.drop_index("uq_documents_repository_source_identity", table_name="documents")
    op.drop_column("documents", "source_revision")
    op.drop_column("documents", "source_identity")
