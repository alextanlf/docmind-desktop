"""durable vector cleanup intents"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.storage.models import UTCDateTime, utc_timestamp_server_default

revision = "0002_vector_cleanups"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table("vector_cleanups", sa.Column("id", sa.String(36), primary_key=True), sa.Column("repository_id", sa.String(36), nullable=False), sa.Column("document_id", sa.String(36), nullable=False, unique=True), sa.Column("vector_ids_json", sa.Text(), nullable=False), sa.Column("created_at", UTCDateTime(), nullable=False, server_default=utc_timestamp_server_default()))

def downgrade() -> None:
    op.drop_table("vector_cleanups")
