"""The Word parser: structure survives, noise does not.

Every case here builds its own ``.docx`` rather than checking in a binary
fixture, so the thing under test is one declared input at a time. The cases are
the ones that actually differ between Word documents in the wild: heading style
written three different ways, two lists in one file, a table, and a title that
lives in metadata rather than the body.
"""
from __future__ import annotations

import zipfile
from io import BytesIO

import pytest

from app.api.errors import DomainError
from app.document.docx import parse_docx
from app.document.builtin_formats import DOCX_MEDIA_TYPE
from app.schemas.imports import DownloadedDocument

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _document(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W}"><w:body>{body}</w:body></w:document>'
    )


def _styles(entries: list[tuple[str, str]]) -> str:
    body = "".join(
        f'<w:style w:styleId="{style_id}"><w:name w:val="{name}"/></w:style>'
        for style_id, name in entries
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:styles xmlns:w="{_W}">{body}</w:styles>'
    )


def _numbering(definitions: list[tuple[str, list[tuple[str, str]]]]) -> str:
    """``[(abstractNumId, [(ilvl, fmt)])]`` → a minimal ``numbering.xml``.

    Each abstract definition gets a ``w:num`` pointing at it under a numId equal
    to the abstract id, which is enough to exercise the two-hop lookup.
    """
    abstracts = "".join(
        f'<w:abstractNum w:abstractNumId="{abstract_id}">'
        + "".join(
            f'<w:lvl w:ilvl="{ilvl}"><w:numFmt w:val="{fmt}"/></w:lvl>'
            for ilvl, fmt in levels
        )
        + "</w:abstractNum>"
        for abstract_id, levels in definitions
    )
    nums = "".join(
        f'<w:num w:numId="{abstract_id}">'
        f'<w:abstractNumId w:val="{abstract_id}"/></w:num>'
        for abstract_id, _ in definitions
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:numbering xmlns:w="{_W}">{abstracts}{nums}</w:numbering>'
    )


def _paragraph(text: str, *, style: str | None = None, outline: str | None = None,
               numbering: tuple[str, str] | None = None) -> str:
    properties = ""
    if style or outline or numbering:
        inner = ""
        if style:
            inner += f'<w:pStyle w:val="{style}"/>'
        if outline:
            inner += f'<w:outlineLvl w:val="{outline}"/>'
        if numbering:
            num_id, ilvl = numbering
            inner += f'<w:numPr><w:ilvl w:val="{ilvl}"/><w:numId w:val="{num_id}"/></w:numPr>'
        properties = f"<w:pPr>{inner}</w:pPr>"
    return f"<w:p>{properties}<w:r><w:t>{text}</w:t></w:r></w:p>"


def _docx(*, body: str, styles: str | None = None, numbering: str | None = None,
          core_title: str | None = None) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", _document(body))
        if styles is not None:
            archive.writestr("word/styles.xml", styles)
        if numbering is not None:
            archive.writestr("word/numbering.xml", numbering)
        if core_title is not None:
            archive.writestr(
                "docProps/core.xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/'
                '2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
                f"<dc:title>{core_title}</dc:title></cp:coreProperties>",
            )
    return buffer.getvalue()


def _parse(raw: bytes, *, title: str = "<uuid>.docx") -> DownloadedDocument:
    document = DownloadedDocument(
        title=title, source_url="staged://abc", media_type=DOCX_MEDIA_TYPE, raw_bytes=raw
    )
    return parse_docx(document)


# -- headings -----------------------------------------------------------------


def test_heading_style_id_maps_to_markdown_levels() -> None:
    raw = _docx(
        body=_paragraph("概述", style="Heading1") + _paragraph("正文一") + _paragraph("细节", style="Heading2"),
        styles=_styles([("Heading1", "heading 1"), ("Heading2", "heading 2")]),
    )

    parsed = _parse(raw)

    assert "## 细节" in parsed.markdown
    assert "# 概述" in parsed.markdown
    # And the section path is built from them, which is what citations key on.
    assert [section.heading_path for section in parsed.sections] == [
        ["概述"],
        ["概述", "细节"],
    ]


def test_localized_style_name_is_recognised() -> None:
    # A Chinese Word template writes 「标题 1」 as the display name while the
    # styleId stays arbitrary. Missing this flattens the whole document.
    raw = _docx(
        body=_paragraph("章节", style="a3") + _paragraph("正文"),
        styles=_styles([("a3", "标题 1")]),
    )

    assert _parse(raw).markdown.startswith("# 章节")


def test_outline_level_is_used_when_no_heading_style_is_present() -> None:
    # Some writers emit only outlineLvl. It is zero-based, so 0 is level 1.
    raw = _docx(body=_paragraph("顶层", outline="0") + _paragraph("次层", outline="1"))

    parsed = _parse(raw)

    assert "# 顶层" in parsed.markdown
    assert "## 次层" in parsed.markdown


def test_title_style_becomes_a_level_one_heading() -> None:
    raw = _docx(
        body=_paragraph("文档标题", style="Title") + _paragraph("正文"),
        styles=_styles([("Title", "Title")]),
    )

    assert _parse(raw).markdown.startswith("# 文档标题")


def test_unknown_style_is_left_as_body_text() -> None:
    # Dirty input: a style that exists but is not a heading must not be
    # promoted, or every captioned paragraph would become a section.
    raw = _docx(
        body=_paragraph("图 1 示意", style="Caption"),
        styles=_styles([("Caption", "caption")]),
    )

    assert _parse(raw).markdown == "图 1 示意"


# -- lists --------------------------------------------------------------------


def test_bullet_and_ordered_lists_use_the_declared_format() -> None:
    raw = _docx(
        body=(
            _paragraph("第一点", numbering=("1", "0"))
            + _paragraph("第二点", numbering=("1", "0"))
            + _paragraph("项目甲", numbering=("2", "0"))
        ),
        numbering=_numbering([("1", [("0", "decimal")]), ("2", [("0", "bullet")])]),
    )

    markdown = _parse(raw).markdown

    assert "1. 第一点" in markdown
    assert "2. 第二点" in markdown
    assert "- 项目甲" in markdown


def test_two_ordered_lists_do_not_share_a_counter() -> None:
    # The regression this guards: a single global counter runs the second list
    # on from the first, so it starts at 3 instead of 1.
    raw = _docx(
        body=(
            _paragraph("甲一", numbering=("1", "0"))
            + _paragraph("甲二", numbering=("1", "0"))
            + _paragraph("乙一", numbering=("2", "0"))
            + _paragraph("乙二", numbering=("2", "0"))
        ),
        numbering=_numbering([("1", [("0", "decimal")]), ("2", [("0", "decimal")])]),
    )

    markdown = _parse(raw).markdown

    assert "1. 甲一" in markdown
    assert "2. 甲二" in markdown
    assert "1. 乙一" in markdown
    assert "2. 乙二" in markdown
    assert "3. 乙一" not in markdown


def test_nested_levels_are_indented_per_level() -> None:
    raw = _docx(
        body=(
            _paragraph("外层", numbering=("1", "0"))
            + _paragraph("内层", numbering=("1", "1"))
        ),
        numbering=_numbering([("1", [("0", "decimal"), ("1", "bullet")])]),
    )

    markdown = _parse(raw).markdown

    assert "1. 外层" in markdown
    assert "  - 内层" in markdown


def test_num_id_zero_means_numbering_removed() -> None:
    # The spec reserves numId 0 for "not numbered"; treating it as a list would
    # turn an ordinary paragraph into a bullet.
    raw = _docx(body=_paragraph("普通段落", numbering=("0", "0")))

    assert _parse(raw).markdown == "普通段落"


# -- tables, runs -------------------------------------------------------------


def test_tables_become_gfm_with_a_header_row() -> None:
    table = (
        "<w:tbl>"
        "<w:tr><w:tc><w:p><w:r><w:t>属性</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>用途</w:t></w:r></w:p></w:tc></w:tr>"
        "<w:tr><w:tc><w:p><w:r><w:t>@State</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>本地状态</w:t></w:r></w:p></w:tc></w:tr>"
        "</w:tbl>"
    )

    markdown = _parse(_docx(body=table)).markdown

    assert "| 属性 | 用途 |" in markdown
    assert "| --- | --- |" in markdown
    assert "| @State | 本地状态 |" in markdown


def test_hyperlink_anchor_text_is_kept() -> None:
    # Reaching for runs instead of the whole subtree silently deletes link text.
    body = (
        "<w:p><w:r><w:t>见</w:t></w:r>"
        '<w:hyperlink r:id="rId4"><w:r><w:t>官方文档</w:t></w:r></w:hyperlink>'
        "<w:r><w:t>说明</w:t></w:r></w:p>"
    )
    document = _document(body).replace("<w:document ", '<w:document xmlns:r="urn:r" ')

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document)

    assert "见官方文档说明" in _parse(buffer.getvalue()).markdown


def test_tabs_and_hard_breaks_are_preserved() -> None:
    body = "<w:p><w:r><w:t>甲</w:t><w:tab/><w:t>乙</w:t><w:br/><w:t>丙</w:t></w:r></w:p>"

    assert "甲\t乙\n丙" in _parse(_docx(body=body)).markdown


def test_empty_paragraphs_do_not_produce_blank_blocks() -> None:
    raw = _docx(body=_paragraph("一") + _paragraph("") + _paragraph("二"))

    assert _parse(raw).markdown == "一\n\n二"


# -- title --------------------------------------------------------------------


def test_title_comes_from_core_properties_not_the_filename() -> None:
    # The importer stages under an opaque UUID, so the filename is useless as a
    # title; the metadata is the only place the real one exists.
    raw = _docx(body=_paragraph("正文"), core_title="6G 非地面网络综述")

    assert _parse(raw, title="3f2a9c14-0000-4000-8000-000000000000.docx").title == "6G 非地面网络综述"


def test_filename_is_the_fallback_when_metadata_is_absent() -> None:
    raw = _docx(body=_paragraph("正文"))

    assert _parse(raw, title="report.docx").title == "report.docx"


def test_blank_metadata_title_falls_back_to_the_filename() -> None:
    raw = _docx(body=_paragraph("正文"), core_title="   ")

    assert _parse(raw, title="report.docx").title == "report.docx"


def test_nested_content_control_text_is_not_dropped() -> None:
    body = (
        "<w:sdt><w:sdtContent>"
        + _paragraph("控件里的内容", style="Heading1")
        + "</w:sdtContent></w:sdt>"
    )

    assert "# 控件里的内容" in _parse(_docx(body=body)).markdown


# -- rejection ----------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        b"not a zip at all",
        b"",
    ],
)
def test_invalid_payload_is_rejected(raw: bytes) -> None:
    with pytest.raises(DomainError) as error:
        _parse(raw)

    assert (error.value.code, error.value.retryable) == ("SOURCE_UNSUPPORTED", False)


def test_zip_without_a_body_is_rejected() -> None:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/styles.xml", _styles([]))

    with pytest.raises(DomainError) as error:
        _parse(buffer.getvalue())

    assert error.value.code == "SOURCE_UNSUPPORTED"


def test_malformed_body_xml_is_rejected() -> None:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", "<w:document><unclosed>")

    with pytest.raises(DomainError) as error:
        _parse(buffer.getvalue())

    assert error.value.code == "SOURCE_UNSUPPORTED"


def test_malformed_auxiliary_part_degrades_instead_of_failing() -> None:
    # A broken styles.xml costs heading levels; it must not cost the document.
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", _document(_paragraph("正文")))
        archive.writestr("word/styles.xml", "<w:styles><broken>")

    assert "正文" in _parse(buffer.getvalue()).markdown


def test_source_url_is_carried_through() -> None:
    raw = _docx(body=_paragraph("正文"))

    assert _parse(raw).source_url == "staged://abc"
