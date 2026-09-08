import sqlalchemy as sa
from alembic import op

revision = "0014_phase4d_knowledge_graph"
down_revision = "0013_phase4c_document_versions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "graph_nodes",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("label", sa.String(1024), nullable=False),
        sa.Column("document_id", sa.String(36), nullable=True, index=True),
    )
    op.create_table(
        "graph_edges",
        sa.Column("source_id", sa.String(255), primary_key=True),
        sa.Column("target_id", sa.String(255), primary_key=True),
        sa.Column("relation", sa.String(32), nullable=False),
    )


def downgrade():
    op.drop_table("graph_edges")
    op.drop_table("graph_nodes")
