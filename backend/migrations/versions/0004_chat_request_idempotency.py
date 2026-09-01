"""durable chat request idempotency

Revision ID: 0004_chat_request_idempotency
Revises: 0003_document_mutations
Create Date: 2026-09-01 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.storage.models import UTCDateTime, utc_timestamp_server_default

revision = "0004_chat_request_idempotency"
down_revision = "0003_document_mutations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_requests",
        sa.Column("request_id", sa.String(length=36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(length=36),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("terminal_type", sa.String(length=16), nullable=True),
        sa.Column("terminal_payload_json", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            UTCDateTime(),
            nullable=False,
            server_default=utc_timestamp_server_default(),
        ),
        sa.Column(
            "updated_at",
            UTCDateTime(),
            nullable=False,
            server_default=utc_timestamp_server_default(),
        ),
    )
    op.create_index("ix_chat_requests_session_id", "chat_requests", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_requests_session_id", table_name="chat_requests")
    op.drop_table("chat_requests")
