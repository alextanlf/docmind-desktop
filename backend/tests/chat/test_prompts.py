from app.chat.prompts import build_rag_prompt
from app.schemas.retrieval import RetrievalHit


def _hit(index: int) -> RetrievalHit:
    return RetrievalHit(
        chunk_id=f"chunk-{index}",
        document_id=f"doc-{index}",
        document_title=f"文档 {index}",
        text=f"片段 {index}",
        section_path=f"章节 > {index}",
        page_number=index,
        source_url=f"https://docs.test/{index}",
        vector_score=0.9,
        keyword_score=1.0,
        fused_score=0.03,
    )


def test_prompt_bounds_answer_to_a_safe_cap_of_labeled_sources_with_metadata() -> None:
    prompt = build_rag_prompt("@State 是什么？", [_hit(index) for index in range(1, 12)])

    assert "仅依据提供的文档片段" in prompt
    assert "使用中文回答" in prompt
    assert "当前文档未覆盖" in prompt
    assert "只能使用 [S#]" in prompt
    assert "问题：@State 是什么？" in prompt
    assert "[S1]" in prompt
    assert "标题：文档 1" in prompt
    assert "章节：章节 > 1" in prompt
    assert "页码：1" in prompt
    assert "[S1]" in prompt
    assert "[S10]" in prompt
    assert "[S11]" not in prompt


def test_prompt_marks_missing_section_and_page_explicitly() -> None:
    hit = _hit(1).model_copy(update={"section_path": None, "page_number": None})

    prompt = build_rag_prompt("问题", [hit])

    assert "章节：未提供" in prompt
    assert "页码：未提供" in prompt


def test_web_variant_authorizes_web_citations_and_arbitrates_conflicts() -> None:
    """🔴 有网页证据时，指令必须授权 [W#] 并给出**冲突裁决规则**。

    旧指令在有网页时仍写「仅依据文档片段」「证据不足就回答当前文档未覆盖」，而且允许的
    引用 ID 只列了 [S#]/[M#]。实测后果（deepseek-flash + 真实 Tavily，3 组 × 3 题）：
    文档**写错**时 0/3 纠正 —— 模型完全无视 prompt 里已经躺着的 [W1]…[W5]。
    只把 [W#] 加进列表也不够：只说"指出不一致"，模型就只指出、不裁决。
    """
    prompt = build_rag_prompt("问题", [_hit(1)], has_web=True)

    assert "[W#]" in prompt
    assert "不得添加链接或编造来源" in prompt
    # 公开可验证的外部事实 → 以网页为准（这一条修的是"文档写错"那半边）
    assert "以网页证据为准" in prompt
    # 内部约定反过来以文档为准，否则同名项目的公开信息会带偏端口这类答案
    assert "一律以文档为准" in prompt
    # 🔴 "仅依据文档片段"必须消失 —— 它与同一条消息里注入的网页证据互斥
    assert "仅依据提供的文档片段" not in prompt
    # 拒答条件要放宽：只有文档与网页都没有才拒答，否则"未覆盖就拒答"会压掉联网的用处
    assert "文档与网页都没有相关信息" in prompt


def test_docs_only_variant_does_not_pretend_web_evidence_exists() -> None:
    """没有网页时不能提 [W#]、也不能提"网页证据"——那会把模型引向不存在的东西。"""
    prompt = build_rag_prompt("问题", [_hit(1)])

    assert "仅依据提供的文档片段" in prompt
    assert "[W#]" not in prompt
    assert "以网页证据为准" not in prompt
