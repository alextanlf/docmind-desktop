from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from app.api.errors import DomainError
from app.document.parser import DocumentParser
from app.schemas.imports import DownloadedDocument


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
    pdf = fitz.open()
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
