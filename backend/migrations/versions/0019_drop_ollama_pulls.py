"""Drop the model-pull table.

DocMind no longer downloads models. Users who run a local model already
installed it with the tool that runs it, and only Ollama exposed a pull API at
all, so the table, its progress-projection machinery and its seven endpoints
went away. The data is discarded rather than migrated: a pull is resumable work
in flight, not user content.

Migration 0010 is intentionally left in place — it already ran on deployed
databases, and rewriting history is not something a released app may do.
"""
from __future__ import annotations

from alembic import op

revision = "0019_drop_ollama_pulls"
down_revision = "0018_remote_parent_id"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_table("ollama_pulls")


def downgrade():
    # Recreating the table is not attempted: the columns and constraints are
    # gone from the model layer, so a faithful restore would need the deleted
    # definitions back. Any in-flight pull was already unrecoverable anyway.
    raise NotImplementedError("ollama_pulls is not restored; re-pulling is a user action")
