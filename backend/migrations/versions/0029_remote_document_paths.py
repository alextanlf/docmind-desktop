"""远端目录路径：文档记住自己在哪个目录，同步快照也记住。

「按目录移除」要落地，路径必须一路存在两处，缺一处就会失效：

* ``documents.remote_path_json`` —— 界面按它分组、按它做前缀移除。
* ``repository_sync_state.path_json`` —— 同步的判重依据是这份"上次见到的远端
  状态"。路径不进快照的话，远端把文档从 A 目录挪到 B 目录时同步会认为"什么都没
  变"，界面上的树就永远停在旧位置。

两者都默认 `'[]'`（即根目录），所以已有数据无需回填语义：它们本来就没记过目录，
显示在根目录下与事实一致 —— 下一次同步会把真实路径写进来。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0029_remote_document_paths"
down_revision = "0028_removed_remote_documents"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "documents",
        sa.Column(
            "remote_path_json",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.add_column(
        "repository_sync_state",
        sa.Column(
            "path_json",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade():
    op.drop_column("repository_sync_state", "path_json")
    op.drop_column("documents", "remote_path_json")
