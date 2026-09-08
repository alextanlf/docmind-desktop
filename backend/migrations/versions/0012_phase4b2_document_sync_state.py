import sqlalchemy as sa
from alembic import op

revision = "0012_phase4b2_document_sync_state"
down_revision = "0011_phase4b1_sync_state"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "documents",
        sa.Column("sync_state", sa.String(32), nullable=False, server_default=sa.text("'synced'")),
    )
    op.add_column(
        "documents",
        sa.Column("local_dirty", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )


def downgrade():
    op.drop_column("documents", "local_dirty")
    op.drop_column("documents", "sync_state")
