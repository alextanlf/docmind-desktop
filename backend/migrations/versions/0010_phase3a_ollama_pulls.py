import sqlalchemy as sa
from alembic import op

from app.storage.models import UTCDateTime

revision = "0010_phase3a_ollama_pulls"
down_revision = "0009_phase2_memory_contract"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "ollama_pulls",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("model_name", sa.String(200), nullable=False),
        sa.Column("base_url", sa.String(512), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(256)),
        sa.Column("total_bytes", sa.Integer()), sa.Column("completed_bytes", sa.Integer()),
        sa.Column("error_code", sa.String(128)), sa.Column("error_message", sa.Text()),
        sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", UTCDateTime(), nullable=False), sa.Column("started_at", UTCDateTime()),
        sa.Column("completed_at", UTCDateTime()), sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.Column("last_event_sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("progress >= 0 AND progress <= 100", name="ck_ollama_pulls_progress"),
        sa.CheckConstraint(
            "state IN ('queued','running','completed','failed','cancelled')",
            name="ck_ollama_pulls_state",
        ),
        sa.CheckConstraint(
            "last_event_sequence >= 0", name="ck_ollama_pulls_event_sequence"
        ),
        sa.CheckConstraint(
            "total_bytes IS NULL OR total_bytes >= 0", name="ck_ollama_pulls_total_bytes"
        ),
        sa.CheckConstraint(
            "completed_bytes IS NULL OR completed_bytes >= 0",
            name="ck_ollama_pulls_completed_bytes",
        ),
    )
    # A normalized Ollama endpoint can process only one pull at a time.  The
    # partial index keeps completed/failed history while protecting active
    # work from races between concurrent requests.
    op.create_index(
        "uq_ollama_pulls_active_base_url",
        "ollama_pulls",
        ["base_url"],
        unique=True,
        sqlite_where=sa.text("state IN ('queued','running')"),
    )

def downgrade():
    op.drop_index("uq_ollama_pulls_active_base_url", table_name="ollama_pulls")
    op.drop_table("ollama_pulls")
