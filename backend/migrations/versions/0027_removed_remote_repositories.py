"""用户主动移除的远端知识库。

`GET /api/repositories` 会把 provider 返回的远端仓库都 upsert 进来，所以"删掉
一个远端绑定的知识库"这件事本身不成立 —— 下一次刷新列表它就回来了。这张表记的
就是那次移除：`(provider, remote_id)` 是唯一的，列表同步据此跳过，恢复即删行。

存 `name` 是为了"已移除"列表里能显示用户认得的名字；只剩两个 id 的话，用户没法
判断该不该恢复。远端内容不在这张表的语义里 —— 它只记录本地的意愿。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.storage.models import UTCDateTime, utc_timestamp_server_default

revision = "0027_removed_remote_repositories"
down_revision = "0026_linked_originals"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "removed_remote_repositories",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("remote_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column(
            "removed_at",
            UTCDateTime(),
            nullable=False,
            server_default=utc_timestamp_server_default(),
        ),
        sa.UniqueConstraint("provider", "remote_id", name="uq_removed_remote_repositories"),
    )


def downgrade():
    op.drop_table("removed_remote_repositories")
