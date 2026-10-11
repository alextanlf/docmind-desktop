"""Record *why* a crawled URL was rejected.

`crawl_entries.error_code` only ever held `CRAWL_FETCH_FAILED` for a network
failure: the fine-grained reason (the site rejected us / we could not reach it /
we were throttled / it is geo-blocked) was computed at failure time and thrown
away with the in-memory discovery result. The user could therefore not be told
which of those happened, and they need different actions — change the request,
change the source, or wait and retry.

Nullable on purpose: rows written before this migration have no reason, and
NULL means "no reason was recorded", which is not the same claim as "the fetch
failed". Readers derive a reason from `error_code` for those rows instead of
inventing one.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_crawl_rejection_reason"
down_revision = "0019_drop_ollama_pulls"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "crawl_entries",
        sa.Column("rejection_reason", sa.String(32), nullable=True),
    )


def downgrade():
    op.drop_column("crawl_entries", "rejection_reason")
