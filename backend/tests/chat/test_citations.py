from app.chat.citations import URLStreamSanitizer, parse_citations, strip_model_urls
from app.schemas.chat import CitationRef


def _source(source_id: str) -> CitationRef:
    return CitationRef(
        source_id=source_id,
        chunk_id=f"chunk-{source_id}",
        document_id=f"doc-{source_id}",
        title="SwiftUI",
        section_path="状态管理 > @State",
        page_number=None,
        excerpt="@State 管理视图拥有的状态。",
        source_url="https://docs.test/state",
    )


def test_citation_parser_keeps_known_ids_in_first_appearance_order() -> None:
    sources = {"S1": _source("S1"), "S2": _source("S2")}

    citations = parse_citations("答案 [S2] [S1] [S2] [S9]", sources)

    assert [item.source_id for item in citations] == ["S2", "S1"]


def test_citation_parser_discards_malformed_ids() -> None:
    sources = {"S1": _source("S1")}

    citations = parse_citations("[S01] [S1x] [S1 [S-1] [[S1]]", sources)

    assert citations == []


def test_model_generated_urls_are_removed_from_answer_text() -> None:
    answer = "参见 https://evil.test/path?q=1 ，结论仍由文档支持 [S1]。"

    assert strip_model_urls(answer) == "参见  ，结论仍由文档支持 [S1]。"


def test_url_sanitizer_removes_case_insensitive_url_split_across_chunks() -> None:
    sanitizer = URLStreamSanitizer()

    answer = "".join(
        [
            sanitizer.feed("依据 HTTPS"),
            sanitizer.feed("://evil.test/path "),
            sanitizer.feed("回答 [S1]"),
            sanitizer.finish(),
        ]
    )

    assert answer == "依据  回答 [S1]"
