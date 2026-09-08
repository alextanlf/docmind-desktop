import sqlalchemy as sa
from alembic import op

from app.storage.models import UTCDateTime

revision = "0013_phase4c_document_versions"
down_revision = "0012_phase4b2_document_sync_state"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "document_versions",
        sa.Column("document_id", sa.String(36), primary_key=True),
        sa.Column("version_no", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(1024), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("document_versions")
