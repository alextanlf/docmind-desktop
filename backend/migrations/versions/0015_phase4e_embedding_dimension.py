import sqlalchemy as sa
from alembic import op

revision = "0015_phase4e_embedding_dimension"
down_revision = "0014_phase4d_knowledge_graph"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "embedding_rebuild",
        sa.Column("document_id", sa.String(36), primary_key=True),
        sa.Column("needs_rebuild", sa.Boolean(), nullable=False, server_default=sa.text("1")),
    )


def downgrade():
    op.drop_table("embedding_rebuild")
