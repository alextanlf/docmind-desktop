"""remote discovery crawl frontier"""
from __future__ import annotations
import sqlalchemy as sa
from alembic import op
from app.storage.models import UTCDateTime, utc_timestamp_server_default

revision = "0006_phase2_remote_discovery"
down_revision = "0005_phase2_batch_core"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "crawl_entries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("batch_id", sa.String(36), sa.ForeignKey("batch_imports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("state", sa.String(16), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("status_code", sa.Integer()), sa.Column("etag", sa.Text()), sa.Column("last_modified", sa.Text()),
        sa.Column("cache_ref_json", sa.Text()), sa.Column("error_code", sa.String(128)),
        sa.Column("created_at", UTCDateTime(), nullable=False, server_default=utc_timestamp_server_default()),
        sa.Column("fetched_at", UTCDateTime()), sa.Column("updated_at", UTCDateTime(), nullable=False, server_default=utc_timestamp_server_default()),
        sa.UniqueConstraint("batch_id", "canonical_url", name="uq_crawl_entries_batch_canonical_url"),
        sa.CheckConstraint("state IN ('pending','fetching','fetched','rejected','failed')", name="ck_crawl_entries_state"),
    )
    op.create_index("ix_crawl_entries_batch_id", "crawl_entries", ["batch_id"])

def downgrade() -> None:
    op.drop_index("ix_crawl_entries_batch_id", table_name="crawl_entries")
    op.drop_table("crawl_entries")
