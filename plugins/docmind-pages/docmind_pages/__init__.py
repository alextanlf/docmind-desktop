"""DocMind's Pages document format.

Adds ``.pages`` to what DocMind can import. It is a plugin because it only works
on macOS with Pages installed, which is a small share of users — shipping it in
the application would charge everyone for a capability most of them cannot use.

The format is a zip of protobuf the application cannot read, so the conversion is
delegated to Pages itself (see :mod:`docmind_pages.pages`), and the result is
handed to DocMind's own Word parser. Nothing about how a ``.docx`` is read is
reimplemented here: if the export improves, so does this, with no change.

The plugin declares an **availability**, so the file picker does not offer
``.pages`` on a machine that could not import it. That is the difference between
a button that is absent and a button that always fails.
"""
from __future__ import annotations

from app.api.errors import DomainError
from app.document.docx import parse_docx
from app.document.formats import DocumentFormat
from app.plugins import DocumentFormatContribution
from app.schemas.imports import DownloadedDocument, ParsedDocument
from docmind_pages import pages

MEDIA_TYPE = "application/vnd.apple.pages"

#: What the exported Word document claims to be. Named here rather than imported
#: from the built-in table: a plugin depends on DocMind's public surface, not on
#: one of its internal constants.
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def parse_pages(document: DownloadedDocument) -> ParsedDocument:
    """Convert one Pages document into the host's document shape."""
    try:
        export = pages.export_docx(document.raw_bytes)
    except pages.PagesUnavailable as error:
        raise DomainError("SOURCE_UNSUPPORTED", str(error), 400, False) from None
    except pages.PagesTimeout as error:
        # Worth retrying: a dialog left open is a transient state, unlike a
        # document that cannot be converted at all.
        raise DomainError("PARSE_TIMEOUT", str(error), 408, True) from None
    except pages.PagesExportFailed as error:
        raise DomainError("PARSE_FAILED", str(error), 400, False) from None

    converted = parse_docx(
        DownloadedDocument(
            title=document.title,
            source_url=document.source_url,
            media_type=DOCX_MEDIA_TYPE,
            raw_bytes=export.docx,
        )
    )
    # No title is recovered: the exported Word file has an empty
    # `docProps/core.xml`, and neither Pages nor the package stores the document's
    # name (see the module docstring in `pages.py`). So these documents are named
    # by the staging UUID, like every other format — the fix belongs in staging,
    # not here.
    return converted


plugin = DocumentFormatContribution(
    format=DocumentFormat(
        name="pages",
        label="Pages 文档",
        media_type=MEDIA_TYPE,
        extensions=(".pages",),
        parse=parse_pages,
        binary_payload=True,
        # A `.pages` is a zip, so the signature alone only proves it is a package.
        # `validate` reads its index and requires the element Pages puts its text in.
        magic=b"PK\x03\x04",
        validate=pages.looks_like_pages,
        availability=pages.unavailable_reason,
    ),
    label="Pages 文档",
    summary="把 Apple Pages 文档导入 DocMind",
    hint="需要 macOS 上的 Pages.app；导入时由 Pages 转换为 Word，每份约几秒。",
    tag="文档格式",
    keywords=("pages", "iwork", "apple", "苹果文档"),
    homepage="https://github.com/alextanlf/docmind-desktop",
    version="0.1.0",
)
