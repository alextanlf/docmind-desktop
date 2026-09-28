"""Decouple remote knowledge bases from Yuque.

Renames the Yuque-specific columns on ``repositories`` and ``documents`` to
provider-neutral names, backfills the new ``repositories.provider`` column,
rewrites the ``batch_imports.source_kind`` CHECK (``yuque_repository`` becomes
``remote_repository``) and the ``distillations.target`` CHECK (``yuque``
becomes ``remote``).

The ``repositories`` table is rebuilt with an explicit ``copy_from`` definition
so that the old unnamed single-column unique index on ``yuque_id`` is dropped
in favor of the composite ``(provider, remote_id)`` unique constraint.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.storage.models import utc_timestamp_server_default

revision = "0016_remote_provider_decoupling"
down_revision = "0015_phase4e_embedding_dimension"
branch_labels = None
depends_on = None


def _repositories_before() -> sa.Table:
    """The repositories table as created by 0001 (unique index intentionally
    omitted so the batch rebuild does not carry it over)."""
    meta = sa.MetaData()
    return sa.Table(
        "repositories",
        meta,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("yuque_id", sa.String(255)),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("yuque_url", sa.Text()),
        sa.Column("sync_status", sa.String(64), nullable=False, server_default=sa.text("'unknown'")),
        sa.Column("document_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.String(40), nullable=False, server_default=utc_timestamp_server_default()),
        sa.Column("updated_at", sa.String(40), nullable=False, server_default=utc_timestamp_server_default()),
    )


def upgrade():
    # --- repositories ------------------------------------------------------
    # Pass 1: rename the Yuque-specific columns and add ``provider``.  The
    # rebuild uses an explicit ``copy_from`` that omits the legacy inline
    # UNIQUE on ``yuque_id`` (created by 0001), so that constraint is dropped
    # in favour of the provider-scoped one added below.
    with op.batch_alter_table(
        "repositories", copy_from=_repositories_before(), recreate="always"
    ) as batch:
        batch.alter_column("yuque_id", new_column_name="remote_id")
        batch.alter_column("yuque_url", new_column_name="remote_url")
        batch.add_column(sa.Column("provider", sa.String(32), nullable=True))
    # Every existing remote binding is a Yuque binding.
    op.execute("UPDATE repositories SET provider = 'yuque' WHERE remote_id IS NOT NULL")

    # Pass 2: introduce the composite ``(provider, remote_id)`` uniqueness.
    # This has to be a separate rebuild: during a batch rebuild Alembic keeps
    # a renamed column in its transfer map under the *old* key (``yuque_id``),
    # so a constraint added in the same batch that references the new name
    # (``remote_id``) is silently skipped.
    with op.batch_alter_table("repositories", recreate="always") as batch:
        batch.create_unique_constraint(
            "uq_repositories_provider_remote_id", ["provider", "remote_id"]
        )

    # --- documents (native RENAME COLUMN, no table rebuild) ----------------
    with op.batch_alter_table("documents") as batch:
        batch.alter_column("yuque_id", new_column_name="remote_id")
        batch.alter_column("yuque_url", new_column_name="remote_url")

    # --- batch_imports: source kind CHECK rewrite -------------------------
    with op.batch_alter_table("batch_imports") as batch:
        batch.drop_constraint("ck_batch_imports_source_kind", type_="check")
    op.execute(
        "UPDATE batch_imports SET source_kind = 'remote_repository' "
        "WHERE source_kind = 'yuque_repository'"
    )
    with op.batch_alter_table("batch_imports") as batch:
        batch.create_check_constraint(
            "ck_batch_imports_source_kind",
            "source_kind IN ('staged_directory', 'web', 'remote_repository', 'search_results')",
        )

    # --- distillations: target CHECK rewrite ------------------------------
    with op.batch_alter_table("distillations") as batch:
        batch.drop_constraint("ck_distillations_target", type_="check")
    op.execute("UPDATE distillations SET target = 'remote' WHERE target = 'yuque'")
    with op.batch_alter_table("distillations") as batch:
        batch.create_check_constraint(
            "ck_distillations_target", "target IS NULL OR target IN ('local','remote')"
        )


def downgrade():
    with op.batch_alter_table("distillations") as batch:
        batch.drop_constraint("ck_distillations_target", type_="check")
    op.execute("UPDATE distillations SET target = 'yuque' WHERE target = 'remote'")
    with op.batch_alter_table("distillations") as batch:
        batch.create_check_constraint(
            "ck_distillations_target", "target IS NULL OR target IN ('local','yuque')"
        )

    with op.batch_alter_table("batch_imports") as batch:
        batch.drop_constraint("ck_batch_imports_source_kind", type_="check")
    op.execute(
        "UPDATE batch_imports SET source_kind = 'yuque_repository' "
        "WHERE source_kind = 'remote_repository'"
    )
    with op.batch_alter_table("batch_imports") as batch:
        batch.create_check_constraint(
            "ck_batch_imports_source_kind",
            "source_kind IN ('staged_directory', 'web', 'yuque_repository', 'search_results')",
        )

    with op.batch_alter_table("documents") as batch:
        batch.alter_column("remote_id", new_column_name="yuque_id")
        batch.alter_column("remote_url", new_column_name="yuque_url")

    # Mirrors upgrade(): rename/drop in one rebuild, then add the single-column
    # unique constraint in a second one (same rename/transfer-map limitation).
    with op.batch_alter_table(
        "repositories", copy_from=_repositories_after(), recreate="always"
    ) as batch:
        batch.alter_column("remote_id", new_column_name="yuque_id")
        batch.alter_column("remote_url", new_column_name="yuque_url")
        batch.drop_column("provider")
    with op.batch_alter_table("repositories", recreate="always") as batch:
        batch.create_unique_constraint("uq_repositories_yuque_id", ["yuque_id"])


def _repositories_after() -> sa.Table:
    """The repositories table as it exists after upgrade() (composite unique
    intentionally omitted so the downgrade rebuild drops it together with the
    provider column)."""
    meta = sa.MetaData()
    return sa.Table(
        "repositories",
        meta,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider", sa.String(32)),
        sa.Column("remote_id", sa.String(255)),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("remote_url", sa.Text()),
        sa.Column("sync_status", sa.String(64), nullable=False, server_default=sa.text("'unknown'")),
        sa.Column("document_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.String(40), nullable=False, server_default=utc_timestamp_server_default()),
        sa.Column("updated_at", sa.String(40), nullable=False, server_default=utc_timestamp_server_default()),
    )
