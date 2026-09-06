from __future__ import annotations

from app.schemas.retrieval import RetrievalHit


def build_rag_prompt(query: str, hits: list[RetrievalHit]) -> str:
    instructions = (
        "仅依据提供的文档片段回答问题。使用中文回答；如果证据不足，明确回答"
        "“当前文档未覆盖”。引用只能使用下方已知来源 ID，"
        "文档引用只能使用 [S#]，跨会话记忆使用 [M#]；不得添加链接或编造来源。"
    )
    source_blocks = [
        "\n".join(
            (
                f"[S{index}]",
                f"标题：{hit.document_title}",
                f"章节：{hit.section_path or '未提供'}",
                f"页码：{hit.page_number if hit.page_number is not None else '未提供'}",
                f"内容：{hit.text}",
            )
        )
        for index, hit in enumerate(hits[:5], start=1)
    ]
    context = "\n\n".join(source_blocks) if source_blocks else "（无可用文档片段）"
    return f"{instructions}\n\n文档片段：\n{context}\n\n问题：{query}"
