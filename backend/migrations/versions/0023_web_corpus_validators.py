"""Store HTTP validators so a re-crawl can ask "did this change?" instead of re-reading.

Without them every refresh re-downloads every page, re-chunks it and rewrites its
rows — which also means the corpus is fully rewritten on each crawl, so an
unchanged page silently gets new chunk ids.

Nullable: pages fetched before this migration have no validators, and the next
crawl simply treats them as "no validator" (unconditional fetch, hash decides).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023_web_corpus_validators"
down_revision = "0022_web_corpus"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("web_corpus_pages", sa.Column("etag", sa.Text(), nullable=True))
    op.add_column("web_corpus_pages", sa.Column("last_modified", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("web_corpus_pages", "last_modified")
    op.drop_column("web_corpus_pages", "etag")
