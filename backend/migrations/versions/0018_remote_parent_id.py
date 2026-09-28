"""Add the provider-native parent target for remote writes.

Remote repositories map to a top-level provider container (Feishu: wiki
space).  New documents can optionally be written below one node inside that
container; the node token is stored on the repository so every create path
(import, distillation, conflict copy) targets the same directory.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018_remote_parent_id"
down_revision = "0017_provider_credentials"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "repositories",
        sa.Column("remote_parent_id", sa.String(255), nullable=True),
    )


def downgrade():
    op.drop_column("repositories", "remote_parent_id")
