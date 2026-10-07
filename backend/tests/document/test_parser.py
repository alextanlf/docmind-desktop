from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from app.api.errors import DomainError
from app.document.builtin_formats import builtin_registry
from app.document.formats import DocumentFormat
from app.document.parser import DocumentParser
from app.schemas.imports import DownloadedDocument, ParsedDocument


@pytest.fixture
def parser() -> DocumentParser:
    return DocumentParser()


@pytest.fixture
def html_document() -> DownloadedDocument:
    fixture = Path(__file__).parent / "fixtures" / "article.html"
    return DownloadedDocument(
        title="article.html", source_url="https://docs.test/article", media_type="text/html", raw_bytes=fixture.read_bytes()
    )


def make_pdf(pages: list[str]) -> DownloadedDocument:
    pdf = pymupdf.open()
    for text in pages:
        page = pdf.new_page()
        if text:
            page.insert_text((72, 72), text)
    raw_bytes = pdf.tobytes()
    pdf.close()
    return DownloadedDocument(title="guide.pdf", source_url="https://docs.test/guide.pdf", media_type="application/pdf", raw_bytes=raw_bytes)


def test_html_parser_preserves_heading_code_table_and_source(html_document: DownloadedDocument, parser: DocumentParser) -> None:
    parsed = parser.parse(html_document)
    assert parsed.title == "SwiftUI 状态管理"
    assert "## @State" in parsed.markdown
    assert "```swift" in parsed.markdown
    assert "| 属性 | 用途 |" in parsed.markdown
    assert "网站导航" not in parsed.markdown
    assert "隐藏内容" not in parsed.markdown
    assert "模板内容" not in parsed.markdown
    assert parsed.markdown.count("SwiftUI 状态管理") == 1
    assert parsed.source_url == "https://docs.test/article"


def test_html_parser_removes_case_and_whitespace_insensitive_hidden_styles(parser: DocumentParser) -> None:
    document = DownloadedDocument(
        title="hidden.html",
        source_url="https://docs.test/hidden",
        media_type="text/html",
        raw_bytes=(
            b"<h1>Visible</h1><p style='DISPLAY : none'>display hidden</p>"
            b"<p style='visibility : HIDDEN'>visibility hidden</p><p>kept</p>"
        ),
    )
    parsed = parser.parse(document)
    assert "display hidden" not in parsed.markdown
    assert "visibility hidden" not in parsed.markdown
    assert "kept" in parsed.markdown


def test_pdf_parser_records_only_nonempty_pages_with_one_based_page_numbers(parser: DocumentParser) -> None:
    parsed = parser.parse(make_pdf(["Page one", "", "Page three"]))
    assert [section.page_number for section in parsed.sections] == [1, 3]
    assert [section.markdown for section in parsed.sections] == ["Page one", "Page three"]


def test_markdown_parser_decodes_utf8_bom_and_preserves_heading_paths(parser: DocumentParser) -> None:
    document = DownloadedDocument(
        title="guide.md",
        source_url="file://staged/guide.md",
        media_type="text/markdown",
        raw_bytes="\ufeff# Guide\n\n## Install\n\nRun it.".encode("utf-8"),
    )
    parsed = parser.parse(document)
    assert parsed.title == "Guide"
    assert parsed.markdown.startswith("# Guide")
    assert [section.heading_path for section in parsed.sections] == [["Guide"], ["Guide", "Install"]]


@pytest.mark.parametrize(
    "document",
    [
        DownloadedDocument(title="bad.md", source_url="x", media_type="text/markdown", raw_bytes=b"\xff"),
        DownloadedDocument(title="bad.pdf", source_url="x", media_type="application/pdf", raw_bytes=b"not pdf"),
    ],
)
def test_parser_rejects_invalid_supported_content(parser: DocumentParser, document: DownloadedDocument) -> None:
    with pytest.raises(DomainError) as error:
        parser.parse(document)
    assert (error.value.code, error.value.retryable) == ("SOURCE_UNSUPPORTED", False)


# -- registry-driven dispatch -------------------------------------------------


def test_parser_dispatches_through_the_registry_for_a_new_format() -> None:
    """A format the parser has never heard of is parsed with no code change.

    This is the property the previous if/elif chain could not have: adding a
    format was an edit to the parser itself.
    """

    def parse_bang(document: DownloadedDocument):
        return ParsedDocument(
            title="bang",
            markdown=document.raw_bytes.decode("utf-8"),
            source_url=document.source_url,
            sections=[],
        )

    registry = builtin_registry()
    registry.register(
        DocumentFormat(
            name="bang",
            media_type="text/x-bang",
            extensions=(".bang",),
            parse=parse_bang,
        )
    )
    parser = DocumentParser(registry)

    parsed = parser.parse(
        DownloadedDocument(
            title="x.bang", source_url="staged://x", media_type="text/x-bang", raw_bytes=b"!hello"
        )
    )

    assert parsed.markdown == "!hello"


def test_parser_rejects_a_media_type_no_format_claims() -> None:
    with pytest.raises(DomainError) as error:
        DocumentParser().parse(
            DownloadedDocument(
                title="x", source_url="x", media_type="application/x-nothing", raw_bytes=b"x"
            )
        )

    assert (error.value.code, error.value.retryable) == ("SOURCE_UNSUPPORTED", False)


def test_parser_runs_the_formats_own_payload_check() -> None:
    # The signature check is declared by the format, so a registry-driven
    # parser still rejects it — including for bytes that never went to staging.
    with pytest.raises(DomainError) as error:
        DocumentParser().parse(
            DownloadedDocument(
                title="x", source_url="x", media_type="application/pdf", raw_bytes=b"not a pdf"
            )
        )

    assert error.value.code == "SOURCE_UNSUPPORTED"


def test_parser_built_without_an_argument_uses_the_builtin_formats() -> None:
    parser = DocumentParser()

    assert parser.formats.for_media_type("application/pdf") is not None
    assert parser.formats.for_media_type("application/vnd.openxmlformats-officedocument.wordprocessingml.document") is not None


def test_parser_survives_a_subclass_that_replaces_init() -> None:
    # A wrapper that hooks parse() without chaining __init__ must still work;
    # otherwise instrumenting the parser breaks it in a way that only shows up
    # at import time.
    class Wrapper(DocumentParser):
        def __init__(self) -> None:
            self.calls = 0

    wrapped = Wrapper()
    wrapped.calls += 1

    assert wrapped.formats.for_media_type("text/markdown") is not None
