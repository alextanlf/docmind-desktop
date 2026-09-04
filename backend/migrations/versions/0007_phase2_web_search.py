from __future__ import annotations
import sqlalchemy as sa
from alembic import op
from app.storage.models import UTCDateTime, utc_timestamp_server_default
revision = "0007_phase2_web_search"
down_revision = "0006_phase2_remote_discovery"
branch_labels = None
depends_on = None
def upgrade() -> None:
    op.create_table("web_search_runs",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=False), sa.Column("user_message_id", sa.String(36), nullable=False),
        sa.Column("query", sa.Text(), nullable=False), sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False), sa.Column("error_code", sa.String(128)),
        sa.Column("created_at", UTCDateTime(), nullable=False, server_default=utc_timestamp_server_default()),
        sa.Column("completed_at", UTCDateTime()))
    op.create_index("uq_web_search_runs_request_id", "web_search_runs", ["request_id"], unique=True)
    op.create_table("web_search_results",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("run_id", sa.String(36), sa.ForeignKey("web_search_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False), sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("title", sa.String(512), nullable=False), sa.Column("snippet", sa.Text(), nullable=False), sa.Column("content", sa.Text(), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False, server_default=sa.text("0")), sa.Column("created_at", UTCDateTime(), nullable=False, server_default=utc_timestamp_server_default()))
    op.create_index("ix_web_search_results_run_id", "web_search_results", ["run_id"])
    op.create_index("uq_web_search_results_run_url", "web_search_results", ["run_id", "canonical_url"], unique=True)
def downgrade() -> None:
    op.drop_table("web_search_results"); op.drop_table("web_search_runs")
