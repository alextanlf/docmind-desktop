"""Crawl state for a web source, carried in the database rather than in memory.

Crawling moves to a background task (it is no longer bounded by "the user must be
willing to wait"), so three things have to survive a page refresh or an app
restart: that a crawl is running, how far it has got, and what the last finished
crawl produced.

`crawl_state` also has its own `interrupted` value: when the app exits mid-crawl
the honest thing to show is "this one was cut short", not "never crawled" (which
is what a reset-to-idle would say) and not "failed" (nothing was wrong).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024_web_source_crawl_state"
down_revision = "0023_web_corpus_validators"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "web_sources",
        sa.Column("crawl_state", sa.String(16), nullable=False, server_default=sa.text("'idle'")),
    )
    op.add_column(
        "web_sources",
        sa.Column("crawl_progress_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "web_sources",
        sa.Column("last_summary_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade():
    op.drop_column("web_sources", "last_summary_json")
    op.drop_column("web_sources", "crawl_progress_json")
    op.drop_column("web_sources", "crawl_state")
