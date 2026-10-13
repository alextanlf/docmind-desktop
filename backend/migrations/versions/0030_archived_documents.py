"""归档文档：``documents.archived_at``。

「删除文档」改成「归档」之后需要回答的唯一问题是"这一篇归档了吗、什么时候" ——
一个可空时间戳同时回答了这两个，布尔列答不了第二个（设置页按归档时间倒序排）。

为什么不是把归档的文档搬到另一张表：归档刻意**保留全部数据**（行、分块、向量、
本地文件、版本历史、图谱节点），恢复才是即时的。搬表等于把"取消归档"变成一次
重新导入，那正是归档要避免的事。所以这里只加一列，可见性由读路径过滤 ——
`list_for_repository` / 检索 / 篇数 / 图谱 / 同步跳过。

已有数据无需回填：``NULL`` 就是"没归档"，与事实一致。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.storage.models import UTCDateTime

revision = "0030_archived_documents"
down_revision = "0029_remote_document_paths"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("archived_at", UTCDateTime(), nullable=True))
    op.create_index("ix_documents_archived_at", "documents", ["archived_at"])


def downgrade():
    op.drop_index("ix_documents_archived_at", table_name="documents")
    op.drop_column("documents", "archived_at")
