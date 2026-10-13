"""插图作为一等内容：每个 chunk 记住它有哪些图。

图片内容（识别出来的文字、图内标注）已经随 markdown 进了全文索引，但那只让**文字**
可检索。要把图本身展示给人看，还需要两样东西：

* **磁盘上的图**：解析时把图形区域渲染成 PNG，落在文档自己的目录里。
* **chunk 与图的对应**：一张图属于哪一页、在页面的什么位置。没有这个对应，引用面板
  只能把图当作全文插图列在最后，用户点进一段正文却看不到它配的那张图。

`images_json` 存的是 `ParsedFigure` 的序列化结果 —— 存 JSON 而不是另开一张表，是因为
它们的读写永远是「跟着这个 chunk 一起」，拆表只会多一次连接。

**为什么位置要存 bbox**：原件是 PDF 时，前端可以直接跳到那一页并框出这块区域，而不是
让用户自己在一张 500 页的扫描件里找。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025_document_chunk_images"
down_revision = "0024_web_source_crawl_state"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "document_chunks",
        sa.Column("images_json", sa.Text(), nullable=False, server_default="[]"),
    )


def downgrade():
    op.drop_column("document_chunks", "images_json")
