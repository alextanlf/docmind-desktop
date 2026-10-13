"""用户从 DocMind 移除的远端文档。

远端原文不动，这里记的只是"别再把它同步回来"。需要这张表的原因见
`app/storage/models.py` 的 `RemovedRemoteDocumentRecord`：同步的判重依据是
`repository_sync_state`（上次见到的远端状态），而不是本地还有没有这篇文档，
所以一个还在更新的远端文档会在被本地移除后自己回来。

`repository_id` 带外键并级联，删掉整个知识库时这些记录随之消失。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.storage.models import UTCDateTime, utc_timestamp_server_default

revision = "0028_removed_remote_documents"
down_revision = "0027_removed_remote_repositories"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "removed_remote_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("repository_id", sa.String(36), nullable=False, index=True),
        sa.Column("remote_id", sa.String(255), nullable=False),
        sa.Column("title", sa.String(1024), nullable=False),
        sa.Column(
            "removed_at",
            UTCDateTime(),
            nullable=False,
            server_default=utc_timestamp_server_default(),
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"], ["repositories.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "repository_id", "remote_id", name="uq_removed_remote_documents"
        ),
    )


def downgrade():
    op.drop_table("removed_remote_documents")
