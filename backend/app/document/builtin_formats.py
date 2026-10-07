"""The document formats DocMind ships with.

Every built-in format is declared once here — its suffix, its media type, how
to parse it, and how to recognise its payload. The staging store, the directory
scanner, the downloader and the parser all read these declarations instead of
carrying their own copies, which is what makes adding a format a one-file change
and lets a plugin add one without touching any of them.
"""
from __future__ import annotations

import re

import pymupdf
from bs4 import BeautifulSoup
from markdownify import markdownify

from app.api.errors import DomainError
from app.document.docx import parse_docx
from app.document.extraction import (
    extract_main_content,
    strip_document_noise,
    strip_hidden_content,
)
from app.document.formats import DocumentFormat, FormatRegistry
from app.document.markdown_sections import sections_from_markdown, title_from_markdown
from app.schemas.imports import DownloadedDocument, ParsedDocument, ParsedSection

PDF_MEDIA_TYPE = "application/pdf"
MARKDOWN_MEDIA_TYPE = "text/markdown"
HTML_MEDIA_TYPE = "text/html"
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = b"PK\x03\x04"


def parse_markdown(document: DownloadedDocument) -> ParsedDocument:
    try:
        markdown = document.raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise DomainError(
            "SOURCE_UNSUPPORTED", "Markdown 必须使用 UTF-8 编码", 400, False
        ) from None
    title = title_from_markdown(markdown) or document.title
    return _from_markdown(document, markdown, title)


def parse_html(document: DownloadedDocument) -> ParsedDocument:
    soup = BeautifulSoup(document.raw_bytes, "html.parser")
    strip_hidden_content(soup)
    title_node = soup.find("h1") or soup.title
    title = title_node.get_text(" ", strip=True) if title_node else document.title
    container = extract_main_content(soup)
    if container is not None:
        strip_document_noise(container)
    if soup.head:
        soup.head.decompose()
    target = container if container is not None else soup
    markdown = markdownify(
        str(target), heading_style="ATX", code_language_callback=_code_language
    ).strip()
    languages = [_code_language(code) for code in target.select("pre > code")]
    language_index = 0

    def add_code_language(match: re.Match[str]) -> str:
        nonlocal language_index
        language = languages[language_index] if language_index < len(languages) else None
        language_index += 1
        return f"```{language or ''}{match.group(1)}```"

    markdown = re.sub(r"```(\n.*?\n)```", add_code_language, markdown, flags=re.DOTALL)
    return _from_markdown(document, markdown, title or document.title)


def parse_pdf(document: DownloadedDocument) -> ParsedDocument:
    try:
        pdf = pymupdf.open(stream=document.raw_bytes, filetype="pdf")
    except (RuntimeError, ValueError):
        raise DomainError("SOURCE_UNSUPPORTED", "PDF 文件无效", 400, False) from None
    try:
        sections = [
            ParsedSection(heading_path=[], markdown=text, page_number=index)
            for index, page in enumerate(pdf, start=1)
            if (text := page.get_text("text").strip())
        ]
    finally:
        pdf.close()
    markdown = "\n\n".join(section.markdown for section in sections)
    return ParsedDocument(
        title=document.title,
        markdown=markdown,
        source_url=document.source_url,
        sections=sections,
    )


def _from_markdown(document: DownloadedDocument, markdown: str, title: str) -> ParsedDocument:
    markdown = markdown.strip()
    return ParsedDocument(
        title=title or document.title,
        markdown=markdown,
        source_url=document.source_url,
        sections=sections_from_markdown(markdown),
    )


def _code_language(element: object) -> str | None:
    classes = getattr(element, "get", lambda _: [])("class") or []
    for class_name in classes:
        if class_name.startswith("language-"):
            return class_name.removeprefix("language-")
    return None


# -- payload recognition -----------------------------------------------------
#
# Each returns a message when the bytes are not that format, or None when they
# are. Returning a message rather than raising lets each caller wrap it in its
# own error code: the staging store reports SOURCE_UNSUPPORTED while the
# directory scanner reports BATCH_SOURCE_CHANGED for the same bad payload.


def _looks_like_pdf(raw: bytes) -> str | None:
    return None if raw.startswith(_PDF_MAGIC) else "PDF 文件签名无效"


def _looks_like_docx(raw: bytes) -> str | None:
    return None if raw.startswith(_ZIP_MAGIC) else "Word 文件签名无效"


def _decodes_as_utf8(raw: bytes) -> str | None:
    try:
        raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return "Markdown 必须使用 UTF-8 编码"
    return None


def _decodes_as_utf8_strict(raw: bytes) -> str | None:
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return "文档必须使用 UTF-8 编码"
    return None


BUILTIN_FORMATS: tuple[DocumentFormat, ...] = (
    DocumentFormat(
        name="pdf",
        label="PDF",
        media_type=PDF_MEDIA_TYPE,
        extensions=(".pdf",),
        parse=parse_pdf,
        binary_payload=True,
        magic=_PDF_MAGIC,
        validate=_looks_like_pdf,
    ),
    DocumentFormat(
        name="markdown",
        label="Markdown",
        media_type=MARKDOWN_MEDIA_TYPE,
        extensions=(".md", ".markdown"),
        parse=parse_markdown,
        content_types=("text/x-markdown", "text/plain"),
        validate=_decodes_as_utf8,
    ),
    DocumentFormat(
        name="docx",
        label="Word 文档",
        media_type=DOCX_MEDIA_TYPE,
        extensions=(".docx",),
        parse=parse_docx,
        binary_payload=True,
        magic=_ZIP_MAGIC,
        validate=_looks_like_docx,
    ),
    DocumentFormat(
        name="html",
        label="网页",
        media_type=HTML_MEDIA_TYPE,
        extensions=(".html", ".htm"),
        parse=parse_html,
        content_types=("application/xhtml+xml",),
        # Parsable, and allowed inside an imported directory, but a lone
        # `.html` is not offered as a document to pick.
        single_file=False,
        validate=_decodes_as_utf8_strict,
    ),
)


def builtin_registry() -> FormatRegistry:
    """A registry holding every format the application ships with.

    Built fresh per call: the application passes its own instance around so
    that plugin formats land in it, while standalone callers (a parser built in
    a test) get a registry that cannot have been mutated by anyone else.
    """
    registry = FormatRegistry()
    for format in BUILTIN_FORMATS:
        registry.register(format)
    return registry
