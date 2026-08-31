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


def test_prompt_bounds_answer_to_five_labeled_sources_with_metadata() -> None:
    prompt = build_rag_prompt("@State 是什么？", [_hit(index) for index in range(1, 7)])

    assert "仅依据提供的文档片段" in prompt
    assert "使用中文回答" in prompt
    assert "当前文档未覆盖" in prompt
    assert "只能使用 [S#]" in prompt
    assert "问题：@State 是什么？" in prompt
    assert "[S1]" in prompt
    assert "标题：文档 1" in prompt
    assert "章节：章节 > 1" in prompt
    assert "页码：1" in prompt
    assert "[S5]" in prompt
    assert "[S6]" not in prompt


def test_prompt_marks_missing_section_and_page_explicitly() -> None:
    hit = _hit(1).model_copy(update={"section_path": None, "page_number": None})

    prompt = build_rag_prompt("问题", [hit])

    assert "章节：未提供" in prompt
    assert "页码：未提供" in prompt
