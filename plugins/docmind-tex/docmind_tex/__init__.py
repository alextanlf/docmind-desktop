"""DocMind's TeX document format.

A plugin that adds ``.tex`` to what DocMind can import. It exists as a plugin
rather than in the application because the audience is narrow — a LaTeX author
already knows how to produce a PDF, and the shipped formats should stay the ones
everybody needs.

The conversion lives in :mod:`docmind_tex.latex` and imports nothing from
DocMind, so it can be read and tested on its own. This module is the only part
that knows DocMind exists: it turns the converter's plain strings into the
``ParsedDocument`` the host expects, and uses the host's own section splitter so
that citations in a ``.tex`` document resolve the same way they do in a ``.docx``.
"""
from __future__ import annotations

from app.api.errors import DomainError
from app.document.formats import DocumentFormat
from app.document.markdown_sections import sections_from_markdown
from app.plugins import DocumentFormatContribution
from app.schemas.imports import DownloadedDocument, ParsedDocument
from docmind_tex import latex

MEDIA_TYPE = "text/x-tex"


def parse_tex(document: DownloadedDocument) -> ParsedDocument:
    """Convert one LaTeX file into the host's document shape."""
    try:
        source = document.raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise DomainError("SOURCE_UNSUPPORTED", "TeX 文件必须使用 UTF-8 编码", 400, False) from None

    conversion = latex.convert(source)
    # The document's own title first, then its first heading, then whatever the
    # host called it. A `.tex` file is usually an assignment or a report with no
    # `\title`, and its opening section is a far better name than the staged
    # file's UUID.
    title = conversion.title or document.title
    return ParsedDocument(
        title=title,
        markdown=conversion.markdown,
        source_url=document.source_url,
        sections=sections_from_markdown(conversion.markdown),
    )


def _decodes_as_utf8(raw: bytes) -> str | None:
    """Reject anything that is not text.

    LaTeX is a text format, so a payload that will not decode is not a LaTeX file
    that was saved oddly — it is some other file with the wrong suffix, and
    saying so at staging time is better than producing a document full of
    replacement characters.
    """
    try:
        raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return "TeX 文件必须使用 UTF-8 编码"
    return None


plugin = DocumentFormatContribution(
    format=DocumentFormat(
        name="tex",
        label="TeX 文档",
        media_type=MEDIA_TYPE,
        extensions=(".tex", ".latex"),
        parse=parse_tex,
        content_types=("text/x-latex", "application/x-tex"),
        validate=_decodes_as_utf8,
    ),
    label="TeX 文档",
    summary="把 LaTeX 源文件导入 DocMind，不需要先编译成 PDF",
    hint="支持 section 层级、表格、列表、代码块与行内/行间公式。",
    tag="文档格式",
    keywords=("latex", "tex", "论文", "排版"),
    homepage="https://github.com/alextanlf/docmind-desktop",
    version="0.1.0",
)
