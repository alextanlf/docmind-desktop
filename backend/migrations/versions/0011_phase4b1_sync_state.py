import sqlalchemy as sa
from alembic import op

from app.storage.models import UTCDateTime

revision = "0011_phase4b1_sync_state"
down_revision = "0010_phase3a_ollama_pulls"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "repository_sync_state",
        sa.Column("repository_id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(512), primary_key=True),
        sa.Column("title", sa.String(1024), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("url", sa.String(1024), nullable=False),
        sa.Column("last_seen_at", UTCDateTime(), nullable=False),
    )
    op.create_table(
        "repository_sync_meta",
        sa.Column("repository_id", sa.String(36), primary_key=True),
        sa.Column("last_synced_at", UTCDateTime(), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("remote_deleted", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )


def downgrade():
    op.drop_column("documents", "remote_deleted")
    op.drop_table("repository_sync_meta")
    op.drop_table("repository_sync_state")
