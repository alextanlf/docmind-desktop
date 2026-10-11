"""The web sources the user decided to include in the local index.

Derivation (`app/corpus/`) answers "which sites are worth crawling"; this table
is where the user's answer is kept. Without it the candidate list is a view that
evaporates on refresh, and the next step (crawling) would have nothing to work
on.

`domain` is the primary key on purpose: candidates are identified by domain
everywhere else, so re-adding one is an update, not a duplicate row.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.storage.models import UTCDateTime, utc_timestamp_server_default

revision = "0021_web_sources"
down_revision = "0020_crawl_rejection_reason"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "web_sources",
        sa.Column("domain", sa.String(253), primary_key=True),
        sa.Column("entry_url", sa.Text(), nullable=True),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(32), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column(
            "added_at", UTCDateTime(), nullable=False, server_default=utc_timestamp_server_default()
        ),
        sa.Column(
            "updated_at", UTCDateTime(), nullable=False, server_default=utc_timestamp_server_default()
        ),
    )


def downgrade():
    op.drop_table("web_sources")
