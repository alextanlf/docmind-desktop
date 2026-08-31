"""durable document mutation compensation intents"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.storage.models import UTCDateTime, utc_timestamp_server_default

revision = "0003_document_mutations"
down_revision = "0002_vector_cleanups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_mutations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("repository_id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False, server_default=utc_timestamp_server_default()),
        sa.Column("updated_at", UTCDateTime(), nullable=False, server_default=utc_timestamp_server_default()),
    )
    op.create_index("ix_document_mutations_document_id", "document_mutations", ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_document_mutations_document_id", table_name="document_mutations")
    op.drop_table("document_mutations")
