from __future__ import annotations

from app.schemas.retrieval import RetrievalHit

# 没有外部证据时的指令。与有网页时**刻意不同**：没有网页可依据，"仅依据文档片段"
# 才是对的，也不该提 [W#]。
_INSTRUCTION_DOCS_ONLY = (
    "仅依据提供的文档片段回答问题。使用中文回答；如果证据不足，明确回答"
    "“当前文档未覆盖”。引用只能使用下方已知来源 ID，"
    "文档引用只能使用 [S#]，跨会话记忆使用 [M#]；不得添加链接或编造来源。"
)

# 🔴 有网页证据时的指令。改这里之前先读 A/B 实测（backend/scripts/ab_web_vs_local.py）：
#
# 旧版在有网页时仍然写「仅依据提供的文档片段」「证据不足就回答当前文档未覆盖」，
# 而且**允许的引用 ID 只列了 [S#]/[M#]、没列 [W#]**。后果实测过（deepseek-flash +
# 真实 Tavily，3 组 × 3 题）：
#
#   - 文档**写错**时（bge-m3 维度、Tavily 是否需要 Key）：0/3 纠正 —— 模型严格遵循
#     "仅依据文档片段"，**完全无视 prompt 里的 [W1]…[W5]**，照抄文档的错误值。
#   - 文档**没覆盖**时：2/3 偶尔用网页、1/3 拒答 —— 行为不可预期。
#     （文档里没有任何可依据内容时，"拒答"与网页证据冲突，模型才转向网页。）
#   - 文档**能答**时：不受影响。
#
# 关键不是"把 [W#] 加进允许列表"就够了。只写"指出不一致"时，模型**只指出、不裁决**，
# 最后仍写"以文档为准"。要真能纠正，必须给出**冲突裁决规则**。
#
# 这条规则按信息类型分工，而不是笼统地"以文档为准"或"以网页为准"：
#   - 内部约定（端口、路径、上限、流程）→ 文档是唯一权威，公开网页里根本不会有；
#   - 公开可验证事实（库版本与用法、API 能力、许可证、模型规格）→ 文档会过时，以网页为准。
# 笼统的"网页优先"同样能修好公开事实那半边，但它会在内部约定上把权威交给网页 ——
# 万一网上有同名项目，端口之类的答案就会被带偏。所以这里选了更保守的分工版。
_INSTRUCTION_WITH_WEB = (
    "依据提供的文档片段回答问题，使用中文回答。请区分两类信息来决定取舍："
    "（一）文档中的内部约定，例如监听端口、路径、容量上限、内部流程等私有事实，一律以文档为准；"
    "（二）公开可验证的外部事实，例如第三方库的版本与用法、API 能力、开源许可证、模型规格等，"
    "若网页证据与文档冲突，则以网页证据为准，并明确指出文档中的说法已经过时。"
    "文档片段不足以回答时，可以使用网页证据补充。"
    "引用只能使用下方已知来源 ID：文档引用用 [S#]，网页引用用 [W#]，跨会话记忆用 [M#]；"
    "不得添加链接或编造来源。只有文档与网页都没有相关信息时，"
    "才回答“当前文档未覆盖该问题”。"
)


def build_rag_context(hits: list[RetrievalHit], *, has_web: bool) -> str:
    """指令 + 文档片段，**不含问题**。

    问题刻意留给调用方放到最末：`_history_with_prompt` 还会在这后面追加
    `<memory-context>` 与 `<web-context>`。把问题夹在中间、再让证据跟在它后面，
    等于把"要回答什么"这个提示埋在一大段资料中间。
    """
    instruction = _INSTRUCTION_WITH_WEB if has_web else _INSTRUCTION_DOCS_ONLY
    # 检索层已把结果收敛在「top-k 正文块 + 至多 N 个文档概览块」以内，
    # 这里保留一个较宽的安全上限，不再用 5 截断——否则前置的概览块会把正文块挤出 prompt。
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
        for index, hit in enumerate(hits[:10], start=1)
    ]
    context = "\n\n".join(source_blocks) if source_blocks else "（无可用文档片段）"
    return f"{instruction}\n\n文档片段：\n{context}"


def build_rag_prompt(query: str, hits: list[RetrievalHit], *, has_web: bool = False) -> str:
    """只有文档、没有记忆与网页时的完整 prompt（诊断脚本与对照实验在用）。"""
    return f"{build_rag_context(hits, has_web=has_web)}\n\n问题：{query}"
