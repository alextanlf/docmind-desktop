"""Local web corpus: fetched pages and their searchable chunks.

Crawling a source produces pages; pages produce chunks. Chunks are what search
reads, and they carry their own url/title on purpose — a hit has to cite its
source, and joining pages on every retrieval hit is cost with no benefit given
that a domain's corpus is replaced wholesale on re-crawl.

Also adds crawl status to ``web_sources``: without it a source whose crawl keeps
failing looks exactly like one that was just added.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.storage.models import UTCDateTime, utc_timestamp_server_default

revision = "0022_web_corpus"
down_revision = "0021_web_sources"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "web_corpus_pages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("domain", sa.String(253), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("media_type", sa.String(64), nullable=False, server_default=sa.text("'text/html'")),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=sa.text("''")),
        sa.Column("byte_size", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "fetched_at", UTCDateTime(), nullable=False, server_default=utc_timestamp_server_default()
        ),
        sa.UniqueConstraint("domain", "url", name="uq_web_corpus_pages_domain_url"),
    )
    op.create_index("ix_web_corpus_pages_domain", "web_corpus_pages", ["domain"])

    op.create_table(
        "web_corpus_chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "page_id",
            sa.String(36),
            sa.ForeignKey("web_corpus_pages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("domain", sa.String(253), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("section_path", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.create_index("ix_web_corpus_chunks_page_id", "web_corpus_chunks", ["page_id"])
    op.create_index("ix_web_corpus_chunks_domain", "web_corpus_chunks", ["domain"])

    op.add_column("web_sources", sa.Column("last_crawled_at", UTCDateTime(), nullable=True))
    op.add_column("web_sources", sa.Column("last_error", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("web_sources", "last_error")
    op.drop_column("web_sources", "last_crawled_at")
    op.drop_index("ix_web_corpus_chunks_domain", table_name="web_corpus_chunks")
    op.drop_index("ix_web_corpus_chunks_page_id", table_name="web_corpus_chunks")
    op.drop_table("web_corpus_chunks")
    op.drop_index("ix_web_corpus_pages_domain", table_name="web_corpus_pages")
    op.drop_table("web_corpus_pages")
