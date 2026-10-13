"""链接型原件：文档记住用户在磁盘上的那个文件，而不是自己留一份副本。

导入改成直读原件之后，`raw_path`（"我们自己存的那份副本"）不再是原件的唯一来源。
新增两列：

* `source_path`：用户磁盘上的绝对路径。**只读、永不删、永不改** —— 删除文档时也只允许
  动 `documents/<id>/` 这一棵树，不许碰它。
* `original_revision`：导入那一刻的 `mtime_ns:size`。用它回答"原件后来被改过吗"。
  存 mtime 而不是内容哈希：为一次状态查询去重算 100 MB 的哈希是不合理的，
  而 mtime 的失真方向（被 touch）只会导致一次多余的"要重新转换吗"，不会静默丢改动 ——
  真正的正确性由"重新转换"那一步重新读原件保证。

与 `raw_path` 的关系：两者互斥，`raw_path` 是历史字段（已经导入的文档仍在用），
新导入的链接型文档只写 `source_path`。解析入口见 `api/documents.py` 的 `original_path()`。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026_linked_originals"
down_revision = "0025_document_chunk_images"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("source_path", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("original_revision", sa.String(128), nullable=True))


def downgrade():
    op.drop_column("documents", "original_revision")
    op.drop_column("documents", "source_path")
