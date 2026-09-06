import sqlalchemy as sa
from alembic import op

from app.storage.models import UTCDateTime

revision = "0009_phase2_memory_contract"
down_revision = "0008_phase2_memory"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("session_summaries", sa.Column("error_message", sa.Text()))
    op.add_column("session_summaries", sa.Column("source_updated_at", UTCDateTime()))
    with op.batch_alter_table("session_summaries") as batch:
        batch.create_check_constraint("ck_session_summaries_state", "state IN ('pending','generating','ready','stale','failed')")
    for name, column in (
        ("sources_json", sa.Column("sources_json", sa.Text(), server_default="[]")),
        ("target_repository_id", sa.Column("target_repository_id", sa.String(36))),
        ("document_id", sa.Column("document_id", sa.String(36))),
        ("error_code", sa.Column("error_code", sa.String(128))),
        ("error_message", sa.Column("error_message", sa.Text())),
        ("retryable", sa.Column("retryable", sa.Boolean(), server_default=sa.text("0"))),
        ("saved_at", sa.Column("saved_at", UTCDateTime())),
        ("last_event_sequence", sa.Column("last_event_sequence", sa.Integer(), nullable=False, server_default="0")),
    ):
        op.add_column("distillations", column)
    with op.batch_alter_table("distillations") as batch:
        batch.create_foreign_key("fk_distillations_target_repository_id", "repositories", ["target_repository_id"], ["id"], ondelete="SET NULL")
        batch.create_foreign_key("fk_distillations_document_id", "documents", ["document_id"], ["id"], ondelete="SET NULL")
        batch.create_check_constraint("ck_distillations_state", "state IN ('generating','draft','saving','saved','saved_unindexed','failed')")
        batch.create_check_constraint("ck_distillations_target", "target IS NULL OR target IN ('local','yuque')")
    op.add_column("memory_chunks", sa.Column("vector_id", sa.String(512)))
    op.add_column("memory_chunks", sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("memory_chunks", sa.Column("indexed", sa.Boolean(), nullable=False, server_default=sa.text("0")))
    op.add_column("memory_chunks", sa.Column("created_at", UTCDateTime()))
    op.execute("UPDATE memory_chunks SET vector_id = 'legacy-memory:' || id WHERE vector_id IS NULL")
    with op.batch_alter_table("memory_chunks") as batch:
        batch.alter_column("vector_id", nullable=False)
        batch.create_unique_constraint("uq_memory_chunks_vector_id", ["vector_id"])
        batch.create_foreign_key("fk_memory_chunks_repository_id", "repositories", ["repository_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_memory_chunks_vector_id", "memory_chunks", ["vector_id"], unique=True)
    op.add_column("memory_vector_cleanups", sa.Column("source_kind", sa.String(32)))
    op.add_column("memory_vector_cleanups", sa.Column("source_id", sa.String(36)))


def downgrade():
    op.drop_column("memory_vector_cleanups", "source_id")
    op.drop_column("memory_vector_cleanups", "source_kind")
    op.drop_index("ix_memory_chunks_vector_id", table_name="memory_chunks")
    with op.batch_alter_table("memory_chunks") as batch:
        batch.drop_constraint("fk_memory_chunks_repository_id", type_="foreignkey")
        batch.drop_constraint("uq_memory_chunks_vector_id", type_="unique")
    for name in ("created_at", "indexed", "token_count", "vector_id"):
        op.drop_column("memory_chunks", name)
    with op.batch_alter_table("distillations") as batch:
        batch.drop_constraint("ck_distillations_target", type_="check")
        batch.drop_constraint("ck_distillations_state", type_="check")
        batch.drop_constraint("fk_distillations_document_id", type_="foreignkey")
        batch.drop_constraint("fk_distillations_target_repository_id", type_="foreignkey")
    for name in ("last_event_sequence", "saved_at", "retryable", "error_message", "error_code", "document_id", "target_repository_id", "sources_json"):
        op.drop_column("distillations", name)
    op.drop_column("session_summaries", "source_updated_at")
    op.drop_column("session_summaries", "error_message")
    with op.batch_alter_table("session_summaries") as batch:
        batch.drop_constraint("ck_session_summaries_state", type_="check")
