"""The Word parser: structure survives, noise does not.

Every case here builds its own ``.docx`` rather than checking in a binary
fixture, so the thing under test is one declared input at a time. The cases are
the ones that actually differ between Word documents in the wild: heading style
written three different ways, two lists in one file, a table, and a title that
lives in metadata rather than the body.
"""
from __future__ import annotations

import hashlib
import zipfile
from io import BytesIO

import pymupdf
import pytest

from app.api.errors import DomainError
from app.document.builtin_formats import DOCX_MEDIA_TYPE
from app.document.docx import parse_docx
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


# -- 图片 ---------------------------------------------------------------------
#
# Word 的正文里没有任何图片内容，图片在 `word/media/` 下，正文只留一个关系 ID。
# 不处理这条通路，图片里的文字（实测一份 Word 课件能识别出 135 行）对搜索完全不存在。

_IMAGE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _png_bytes(width: int = 400, height: int = 300) -> bytes:
    """一张足够大的 PNG。

    纯色小图压缩后只有几百字节，会被 `_MIN_FIGURE_BYTES`（4 KB）当图标滤掉 —— 所以这里
    铺一层渐变，让它有真实插图的体积。
    """
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height))
    for y in range(height):
        for x in range(0, width, 8):
            pixmap.set_pixel(x, y, ((x * 7) % 256, (y * 5) % 256, ((x + y) * 3) % 256))
    return pixmap.tobytes("png")


def _drawing(rel_id: str, *, descr: str = "", name: str = "Picture 1") -> str:
    return (
        "<w:p><w:r><w:drawing>"
        f'<wp:inline><wp:docPr id="1" name="{name}" descr="{descr}"/>'
        f'<a:graphic><a:graphicData><pic:pic><pic:blipFill>'
        f'<a:blip r:embed="{rel_id}"/>'
        "</pic:blipFill></pic:pic></a:graphicData></a:graphic>"
        "</wp:inline></w:drawing></w:r></w:p>"
    )


def _vml_picture(rel_id: str) -> str:
    return f'<w:p><w:r><w:pict><v:shape><v:imagedata r:id="{rel_id}"/></v:shape></w:pict></w:r></w:p>'


def _docx_with_images(
    body: str, images: dict[str, bytes], *, rel_ids: list[str] | None = None
) -> bytes:
    """合成一份带图片的 .docx。"""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<w:document xmlns:w="{_W}"'
            ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
            ' xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"'
            ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
            ' xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"'
            ' xmlns:v="urn:schemas-microsoft-com:vml">'
            f"<w:body>{body}</w:body></w:document>",
        )
        relationships = "".join(
            f'<Relationship Id="{rel_id}" Type="{_IMAGE_REL}" Target="media/{rel_id}.png"/>'
            for rel_id in (rel_ids if rel_ids is not None else list(images))
        )
        archive.writestr(
            "word/_rels/document.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{_REL_NS}">{relationships}</Relationships>',
        )
        for rel_id, payload in images.items():
            archive.writestr(f"word/media/{rel_id}.png", payload)
    return buffer.getvalue()


def _document_from(raw: bytes) -> DownloadedDocument:
    return DownloadedDocument(
        title="report.docx",
        source_url="file:///report.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        raw_bytes=raw,
    )


def test_docx_image_is_extracted_as_a_figure(monkeypatch: pytest.MonkeyPatch) -> None:
    """图片必须变成 ParsedFigure，否则它永远不会出现在界面上。

    显式设置替代文字是为了绕开识别：这条用例测的是**抽取与归属**，不是识别质量。
    """
    raw = _docx_with_images(
        f"<w:p><w:r><w:t>系统结构说明</w:t></w:r></w:p>{_drawing('rId7', descr='系统结构说明')}",
        {"rId7": _png_bytes()},
    )
    parsed = parse_docx(_document_from(raw))

    assets = [figure.asset for section in parsed.sections for figure in section.figures]
    assert len(assets) == 1
    assert assets[0].endswith(".png")
    assert "系统结构说明" in parsed.markdown


def test_vml_picture_is_also_recognised() -> None:
    """VML（旧式 `w:pict`）也要认。

    实测一份 Word 文档里 226 个 `w:drawing`、207 个 `w:pict` —— 只认一种会漏掉一半。
    """
    raw = _docx_with_images(
        f"<w:p><w:r><w:t>图注</w:t></w:r></w:p>{_vml_picture('rId9')}",
        {"rId9": _png_bytes()},
    )
    parsed = parse_docx(_document_from(raw))

    assert sum(len(section.figures) for section in parsed.sections) == 1


def test_docx_image_alt_text_becomes_searchable_text() -> None:
    """替代文字是零成本的人工描述，必须进正文 —— 有它就不必再识别。"""
    raw = _docx_with_images(
        f"<w:p><w:r><w:t>下面这张图</w:t></w:r></w:p>{_drawing('rId7', descr='模块依赖关系图')}",
        {"rId7": _png_bytes()},
    )
    parsed = parse_docx(_document_from(raw))

    assert "模块依赖关系图" in parsed.markdown


def test_docx_image_text_is_not_indexed_twice() -> None:
    """图内文字与正文重叠时不许重复并入 —— 与 PDF 那条通路同一门槛、同一理由。

    实测图的文字常常就是正文的复制（图注、轴名来自同一段），整段并入会让同一内容被索引
    两遍，检索时返回看起来完全相同的引用。
    """
    body = "Cache replacement policy affects the miss rate"
    raw = _docx_with_images(
        f"<w:p><w:r><w:t>{body}</w:t></w:r></w:p>{_drawing('rId7', descr=body)}",
        {"rId7": _png_bytes()},
    )
    parsed = parse_docx(_document_from(raw))

    assert parsed.markdown.count("Cache replacement policy") == 1


def test_tiny_docx_images_are_not_figures() -> None:
    """图标、项目符号、装饰线不该当内容图。"""
    tinytag = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8)).tobytes("png")
    raw = _docx_with_images(
        f"<w:p><w:r><w:t>正文</w:t></w:r></w:p>{_drawing('rId7')}",
        {"rId7": tinytag},
    )
    parsed = parse_docx(_document_from(raw))

    assert sum(len(section.figures) for section in parsed.sections) == 0


def test_unreferenced_media_is_not_a_figure() -> None:
    """media 目录里有、正文没引用的文件不算图（模板残留、备用图）。"""
    raw = _docx_with_images("<w:p><w:r><w:t>正文</w:t></w:r></w:p>", {"rId7": _png_bytes()})
    parsed = parse_docx(_document_from(raw))

    assert sum(len(section.figures) for section in parsed.sections) == 0


def test_missing_ocr_capability_does_not_break_docx(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有识别能力时仍要能导入 Word —— 图内文字拿不到而已，正文不能因此丢掉。"""
    from app.document import ocr

    def unavailable(images):
        raise ocr.OcrUnavailable("没有可用的识别后端")

    monkeypatch.setattr(ocr, "recognize", unavailable)
    raw = _docx_with_images(
        f"<w:p><w:r><w:t>正文段落</w:t></w:r></w:p>{_drawing('rId7')}",
        {"rId7": _png_bytes()},
    )
    parsed = parse_docx(_document_from(raw))

    assert "正文段落" in parsed.markdown
    # 图还是要在清单里，只是没有文字。
    assert sum(len(section.figures) for section in parsed.sections) == 1


def test_docx_figure_carries_bytes_for_disk_write() -> None:
    """插图必须带着字节，否则落盘那一步写不出文件。

    导入流程靠 `ParsedFigure.payload` 把图写到文档目录（见
    `app/imports/service.py::_write_figures`）。这个字段 `exclude=True` 不参与序列化，
    所以它是「只在内存里活一段」的 —— 一旦在某处被丢掉，图会静默地只剩一个文件名，
    界面上什么都显示不出来。
    """
    raw = _docx_with_images(
        f"<w:p><w:r><w:t>正文</w:t></w:r></w:p>{_drawing('rId7', descr='说明文字')}",
        {"rId7": _png_bytes()},
    )
    parsed = parse_docx(_document_from(raw))

    figures = [figure for section in parsed.sections for figure in section.figures]
    assert len(figures) == 1
    assert figures[0].payload.startswith(b"\x89PNG")
    # 资产名由内容摘要决定：同一张图重复导入会得到同一个文件名。
    assert figures[0].asset.startswith(hashlib.sha256(figures[0].payload).hexdigest())
