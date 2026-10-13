"""Word (``.docx``) → markdown, using only the standard library.

A ``.docx`` is a zip whose ``word/document.xml`` holds the whole paragraph tree,
so reading one needs no new dependency. That matters here: the shipped backend
runtime is ~310 MB of ``site-packages``, and every import added is paid for by
every user on every install. ``python-docx`` would buy convenience that costs a
package we do not need.

What is deliberately modelled, and why none of it is optional:

* **Heading levels** come from three places, because Word writes whichever one
  the document was authored with. ``styles.xml`` gives a ``styleId`` → display
  name mapping (``Heading2`` → "heading 2"; localized templates write 「标题 2」),
  and a paragraph may instead carry only an ``outlineLvl``. Missing any of them
  flattens the document — and a flattened document has no section paths, which
  is what citation quality is built on.
* **Lists** need ``numbering.xml``: ``numPr`` names a ``numId``, which points at
  an ``abstractNumId`` holding the per-level ``numFmt``. Bullets become ``-``
  and everything else becomes an ordered item numbered **per (numId, ilvl)**. A
  single global counter silently runs one list into the next, which is wrong in
  any document with two lists.
* **Tables** become GFM. A table's cells are where the numbers live; dropping
  them turns an evidence-bearing chunk into prose with holes.
* **The title** comes from ``docProps/core.xml`` rather than the filename. The
  importer stages files under an opaque UUID, so the filename is ``<uuid>.docx``
  — the real title would otherwise never enter the index.
"""
from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from xml.etree import ElementTree

import pymupdf

from app.api.errors import DomainError
from app.document import ocr
from app.document.markdown_sections import sections_from_markdown
from app.schemas.imports import (
    DownloadedDocument,
    ParsedDocument,
    ParsedFigure,
    ParsedSection,
)

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_DC_TITLE = "{http://purl.org/dc/elements/1.1/}title"
#: DrawingML（内联图片、文本框）与 VML（旧式 `w:pict`）。两种都要认：实测一份 Word
#: 文档里 `w:drawing` 226 个、`w:pict` 207 个，只认一种会漏掉一半。
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
_PIC = "{http://schemas.openxmlformats.org/drawingml/2006/picture}"
_V = "{urn:schemas-microsoft-com:vml}"
_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"

#: 比这个还小的图不当内容（图标、项目符号、装饰线）。实测文档里的正文插图都远大于此。
_MIN_FIGURE_BYTES = 4 * 1024
#: 图内文字与正文的重叠低于这个比例才并入 —— 与 PDF 那条通路同一门槛、同一理由：
#: 图的文字常常就是正文的复制，整段并入会让同一内容被索引两遍。
_FIGURE_TEXT_NOVELTY = 0.4
#: 图与某一节的词重合低于此比例时，不认为图属于那一节。
_FIGURE_SECTION_MIN_OVERLAP = 0.2
_WORD = re.compile(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]")

#: `Heading1` (styleId) / `heading 1` / `标题 1` — Word's own id and the
#: localized display name. Both appear in real files.
_HEADING_STYLE = re.compile(r"^(?:heading|标题)\s*([1-9])$", re.IGNORECASE)

_MAX_MEMBER_BYTES = 64 * 1024 * 1024


def parse_docx(document: DownloadedDocument) -> ParsedDocument:
    """Parse a ``.docx`` payload into markdown plus heading-derived sections."""
    try:
        archive = zipfile.ZipFile(BytesIO(document.raw_bytes))
    except (zipfile.BadZipFile, ValueError, OSError):
        raise DomainError("SOURCE_UNSUPPORTED", "Word 文件无效", 400, False) from None

    with archive:
        names = set(archive.namelist())
        if "word/document.xml" not in names:
            raise DomainError("SOURCE_UNSUPPORTED", "Word 文件缺少正文", 400, False)
        body_xml = _read_member(archive, "word/document.xml")
        styles = _style_names(_read_member(archive, "word/styles.xml", optional=True))
        numbering = _numbering_formats(_read_member(archive, "word/numbering.xml", optional=True))
        core_title = _core_title(_read_member(archive, "docProps/core.xml", optional=True))
        relationships = _image_relationships(
            _read_member(archive, "word/_rels/document.xml.rels", optional=True)
        )
        figures = _collect_figures(body_xml, archive, relationships)

    _recognize_figures(figures)
    markdown = _convert_body(body_xml, styles, numbering, figures)
    sections = sections_from_markdown(markdown)
    _attach_figures(sections, figures)
    return ParsedDocument(
        title=core_title or document.title,
        markdown=markdown,
        source_url=document.source_url,
        sections=sections,
    )


def _tokens(text: str) -> set[str]:
    return {match.group(0).lower() for match in _WORD.finditer(text)}


def _attach_figures(
    sections: list[ParsedSection], figures: list[DOCXFigure]
) -> None:
    """把插图挂到它们文字所在的那一节上。

    Word 文档没有页码，也没有 PDF 那样的图形区域坐标，所以定位只能靠**文字**：图内文字
    或替代文字出现在哪一节，图就属于哪一节。找不到归属的图挂到第一节 —— 宁可位置粗一点，
    也不要让图从界面上消失。

    不挂到每一节：那会让同一张图在每个 chunk 的引用里都出现一次。
    """
    if not figures or not sections:
        return
    parsed = [
        ParsedFigure(
            asset=figure.asset,
            caption=figure.caption,
            # `text` 必须一起带过去：识别结果填在 `DOCXFigure` 上，而这个转换是构造
            # `ParsedFigure` 的唯一地方 —— 漏掉它就等于识别白跑了（图内文字到了 markdown，
            # 却不在图上，任何读 `figure.text` 的下游都拿到空串）。
            text=figure.text,
            width=figure.width,
            height=figure.height,
            payload=figure.payload,
        )
        for figure in figures
    ]
    # 归属靠文字重合：图内文字（或替代文字）与哪一节共用的词最多，就属于哪一节。
    # 图的文字是**从图里识别出来的**，最可能出现在介绍这张图的那一节里。
    section_tokens = [_tokens(section.markdown) for section in sections]
    unassigned: list[ParsedFigure] = []
    for figure, source in zip(parsed, figures, strict=True):
        # 识别结果在 `text` 上，替代文字在 `caption` 上。两者语义不同：
        # `text` 是"我们识别出来的"，`caption` 是"作者写的描述"。
        figure_tokens = _tokens(source.text) | _tokens(source.caption)
        best_index, best_overlap = -1, 0.0
        if figure_tokens:
            for index, tokens in enumerate(section_tokens):
                if not tokens:
                    continue
                overlap = len(figure_tokens & tokens) / len(figure_tokens)
                if overlap > best_overlap:
                    best_index, best_overlap = index, overlap
        # 重合太少说明这一节的文字和图没对上，宁可挂到第一节也不要乱挂。
        if best_index >= 0 and best_overlap >= _FIGURE_SECTION_MIN_OVERLAP:
            sections[best_index].figures.append(figure)
        else:
            unassigned.append(figure)
    # 找不到归属的（图内没文字、或与任何一节都对不上）挂到第一节 —— 位置粗一点也好过
    # 让图从界面上消失。
    sections[0].figures.extend(unassigned)


def _read_member(archive: zipfile.ZipFile, name: str, *, optional: bool = False) -> bytes:
    """Read one member, with a size ceiling.

    A docx is untrusted input and the declared size is attacker-controlled, so
    the ceiling is checked before decompressing rather than after: reading a
    zip bomb to find out it was one is the failure mode this avoids.
    """
    try:
        info = archive.getinfo(name)
    except KeyError:
        if optional:
            return b""
        raise
    if info.file_size > _MAX_MEMBER_BYTES:
        raise DomainError("DOCUMENT_TOO_LARGE", "Word 文件正文过大", 413, False)
    try:
        return archive.read(info)
    except (zipfile.BadZipFile, RuntimeError, OSError, EOFError):
        raise DomainError("SOURCE_UNSUPPORTED", "Word 文件正文损坏", 400, False) from None


def _parse_xml(raw: bytes) -> ElementTree.Element | None:
    if not raw:
        return None
    try:
        return ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        # A malformed auxiliary part degrades the conversion; a malformed body
        # is the caller's problem and is checked separately.
        return None


def _style_names(styles_xml: bytes) -> dict[str, str]:
    """``styleId`` → lowercased display name, for heading detection."""
    root = _parse_xml(styles_xml)
    if root is None:
        return {}
    names: dict[str, str] = {}
    for style in root.findall(f"{_W}style"):
        style_id = style.get(f"{_W}styleId")
        name = style.find(f"{_W}name")
        if style_id and name is not None:
            names[style_id] = (name.get(f"{_W}val") or "").strip().lower()
    return names


def _numbering_formats(numbering_xml: bytes) -> dict[tuple[str, str], str]:
    """``(numId, ilvl)`` → ``numFmt``, e.g. ``("5", "0") -> "decimal"``.

    Two hops, because that is how the format is written: ``w:num`` maps a
    ``numId`` onto an ``abstractNumId``, and the abstract definition holds the
    per-level format. A level with no ``numFmt`` is treated as ``decimal``,
    which is the spec's default rather than a guess.
    """
    root = _parse_xml(numbering_xml)
    if root is None:
        return {}
    abstract: dict[str, dict[str, str]] = {}
    for node in root.findall(f"{_W}abstractNum"):
        abstract_id = node.get(f"{_W}abstractNumId")
        if abstract_id is None:
            continue
        levels: dict[str, str] = {}
        for level in node.findall(f"{_W}lvl"):
            ilvl = level.get(f"{_W}ilvl") or "0"
            fmt = level.find(f"{_W}numFmt")
            levels[ilvl] = (fmt.get(f"{_W}val") if fmt is not None else None) or "decimal"
        abstract[abstract_id] = levels

    formats: dict[tuple[str, str], str] = {}
    for node in root.findall(f"{_W}num"):
        num_id = node.get(f"{_W}numId")
        reference = node.find(f"{_W}abstractNumId")
        if num_id is None or reference is None:
            continue
        for ilvl, fmt in abstract.get(reference.get(f"{_W}val") or "", {}).items():
            formats[(num_id, ilvl)] = fmt
    return formats


def _core_title(core_xml: bytes) -> str | None:
    root = _parse_xml(core_xml)
    if root is None:
        return None
    node = root.find(_DC_TITLE)
    title = (node.text or "").strip() if node is not None else ""
    return title or None


def _convert_body(
    body_xml: bytes,
    styles: dict[str, str],
    numbering: dict[tuple[str, str], str],
    figures: list[DOCXFigure] = (),
) -> str:
    try:
        root = ElementTree.fromstring(body_xml)
    except ElementTree.ParseError:
        raise DomainError("SOURCE_UNSUPPORTED", "Word 文件正文损坏", 400, False) from None
    body = root.find(f"{_W}body")
    if body is None:
        return ""
    blocks: list[str] = []
    _walk(
        body,
        styles,
        numbering,
        {},
        blocks,
        {figure.figure_id: figure for figure in figures},
        set(),
    )
    return "\n\n".join(block for block in blocks if block.strip())


def _walk(
    parent: ElementTree.Element,
    styles: dict[str, str],
    numbering: dict[tuple[str, str], str],
    counters: dict[tuple[str, str], int],
    blocks: list[str],
    figures: dict[str, DOCXFigure],
    seen_tokens: set[str],
) -> None:
    """Emit every paragraph and table under ``parent``.

    Nested containers (``w:sdt`` content controls, tracked-change wrappers) hold
    their paragraphs one level down rather than being paragraphs themselves, so
    an unknown element is descended into instead of skipped. Skipping would drop
    text from documents that merely wrapped it.
    """
    for child in parent:
        if child.tag == f"{_W}p":
            block = _paragraph(child, styles, numbering, counters)
            if block:
                blocks.append(block)
                seen_tokens |= _tokens(block)
            # 图内文字排在它所属段落之后：段落给出上下文，图给出图里的术语。
            #
            # 只并**新增**的：图的文字常常就是正文的复制（图注、轴名来自同一段），整段并入
            # 会让同一内容被索引两遍，检索时返回看起来完全相同的引用。与 PDF 那条通路
            # 同一门槛、同一理由。
            for figure_id in _figure_ids(child):
                figure = figures.get(figure_id)
                if figure is None or not figure.text:
                    continue
                tokens = _tokens(figure.text)
                if tokens and len(tokens - seen_tokens) / len(tokens) < _FIGURE_TEXT_NOVELTY:
                    continue
                seen_tokens |= tokens
                blocks.append(figure.text)
        elif child.tag == f"{_W}tbl":
            table = _table(child)
            if table:
                blocks.append(table)
        elif len(child):
            _walk(child, styles, numbering, counters, blocks, figures, seen_tokens)


@dataclass
class DOCXFigure:
    """Word 文档里的一张插图。

    ``text`` 是识别或替代文字得到的可检索文本 —— 这是图片进入索引的唯一途径：Word 的
    正文里没有任何图片内容，不识别就等于这张图对搜索完全不存在。实测一份 Word 课件里的
    图片能识别出 135 行文字（课程结构图、软件工程知识体系图）。
    """

    figure_id: str
    asset: str
    payload: bytes
    #: 图内文字：优先用 ``wp:docPr`` 的替代文字，没有就跑识别。
    text: str = ""
    caption: str = ""
    width: int = 0
    height: int = 0


def _image_relationships(rels_xml: bytes) -> dict[str, str]:
    """``rId`` → ``word/media/...`` 的映射。

    图片不在正文里，而是在 ``word/media/`` 下，正文只留一个关系 ID。不做这层映射就只会
    看到一堆 ``rId7``，拿不到任何字节。
    """
    root = _parse_xml(rels_xml)
    if root is None:
        return {}
    mapping: dict[str, str] = {}
    for node in root.findall(f"{_REL_NS}Relationship"):
        if not (node.get("Type") or "").endswith("/image"):
            continue
        identifier = node.get("Id")
        target = node.get("Target")
        if identifier and target:
            # Target 可能是相对的（media/image1.png）也可能带 ../。
            mapping[identifier] = target.replace("../", "")
    return mapping


def _figure_ids(paragraph: ElementTree.Element) -> list[str]:
    """一个段落引用了哪些图片（DrawingML 的 ``a:blip`` 与 VML 的 ``v:imagedata``）。

    两种都要认：实测一份 Word 文档 226 个 ``w:drawing``、207 个 ``w:pict``。
    """
    ids: list[str] = []
    for node in paragraph.iter():
        if node.tag == f"{_A}blip":
            identifier = node.get(f"{_R}embed") or node.get(f"{_R}link")
        elif node.tag == f"{_V}imagedata":
            identifier = node.get(f"{_R}id")
        else:
            continue
        if identifier and identifier not in ids:
            ids.append(identifier)
    return ids


def _alt_text(paragraph: ElementTree.Element) -> str:
    """图片的替代文字。

    作者在 Word 里写的「替换文字」是**零成本的、人工写的描述**，比识别的结果更贴近语义。
    有它就不必再识别。
    """
    for node in paragraph.iter():
        if node.tag == f"{_WP}docPr":
            description = (node.get("descr") or "").strip()
            name = (node.get("name") or "").strip()
            # `name` 常是 "Picture 765022" 这类自动生成的占位名，没有信息量。
            if description:
                return description
            if name and not name.lower().startswith(("picture", "image", "图表", "图片")):
                return name
    return ""


def _collect_figures(
    body_xml: bytes, archive: zipfile.ZipFile, relationships: dict[str, str]
) -> list[DOCXFigure]:
    """把正文引用到的图片全部抽出来。

    按内容摘要命名（与 PDF 那条通路一致）：同一张图在文档里出现多次时只落一个文件。
    """
    try:
        root = ElementTree.fromstring(body_xml)
    except ElementTree.ParseError:
        return []
    body = root.find(f"{_W}body")
    if body is None:
        return []

    # 按 media 路径去重（同一张图在正文里可能出现多次），但对外以**关系 ID** 为键 ——
    # `_walk` 拿到的就是关系 ID，键不一致会永远查不到。
    seen_targets: set[str] = set()
    figures: list[DOCXFigure] = []
    for paragraph in body.iter(f"{_W}p"):
        alt = _alt_text(paragraph)
        for identifier in _figure_ids(paragraph):
            target = relationships.get(identifier)
            if not target or target in seen_targets:
                continue
            seen_targets.add(target)
            payload = _read_media(archive, target)
            if payload is None:
                continue
            normalized = _normalize_image(payload)
            if normalized is None:
                continue
            image_bytes, extension = normalized
            if len(image_bytes) < _MIN_FIGURE_BYTES:
                continue
            digest = hashlib.sha256(image_bytes).hexdigest()
            figures.append(
                DOCXFigure(
                    figure_id=identifier,
                    asset=f"{digest}.{extension}",
                    payload=image_bytes,
                    text=alt,
                    caption=alt,
                )
            )
    return figures


def _read_media(archive: zipfile.ZipFile, target: str) -> bytes | None:
    """读一个媒体成员。读取失败返回 None —— 一张图读不出来不该让整篇文档失败。"""
    name = target if target.startswith("word/") else f"word/{target}"
    try:
        return _read_member(archive, name, optional=True) or None
    except DomainError:
        return None


def _normalize_image(payload: bytes) -> tuple[bytes, str] | None:
    """把图片规范成识别能读的容器，返回 ``(字节, 扩展名)``。

    PNG/JPEG 直接放行。WMF/EMF 必须转换：那是 Windows 上的矢量图（MATLAB、Visio、老版
    Office 贴出来的图），识别引擎读不了，浏览器也显示不了。
    """
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return payload, "png"
    if payload.startswith(b"\xff\xd8\xff"):
        return payload, "jpeg"
    try:
        pixmap = pymupdf.Pixmap(payload)
    except (RuntimeError, ValueError):
        return None
    return pixmap.tobytes("png"), "png"


def _recognize_figures(figures: list[DOCXFigure]) -> None:
    """给没有替代文字的图跑一次识别，把图内文字填进 ``text``。

    整批一次调用：识别进程的启动开销按批摊，逐张调用在 20 张图上就要多花十几秒。
    """
    pending = [figure for figure in figures if not figure.text]
    if not pending:
        return
    images = []
    for figure in pending:
        try:
            pixmap = pymupdf.Pixmap(figure.payload)
        except (RuntimeError, ValueError):
            continue
        images.append(
            ocr.RenderedImage(
                pixels=figure.payload,
                width=float(pixmap.width),
                height=float(pixmap.height),
                page_rect=(0.0, 0.0, float(pixmap.width), float(pixmap.height)),
                page_number=0,
                extension="png" if figure.asset.endswith(".png") else "jpeg",
            )
        )
    try:
        recognized = ocr.recognize(images)
    except ocr.OcrUnavailable:
        # 没有识别能力的机器照样要能导入 Word —— 图内文字拿不到而已。
        return
    for figure, page in zip(pending, recognized, strict=False):
        figure.text = page.text().strip()
        figure.width = int(page.width)
        figure.height = int(page.height)


def _paragraph(
    paragraph: ElementTree.Element,
    styles: dict[str, str],
    numbering: dict[tuple[str, str], str],
    counters: dict[tuple[str, str], int],
) -> str:
    text = _run_text(paragraph).strip()
    if not text:
        return ""
    properties = paragraph.find(f"{_W}pPr")
    level = _heading_level(properties, styles)
    if level is not None:
        return f"{'#' * level} {text}"
    marker = _list_marker(properties, numbering, counters)
    if marker is not None:
        return f"{marker}{text}"
    return text


def _heading_level(properties: ElementTree.Element | None, styles: dict[str, str]) -> int | None:
    if properties is None:
        return None
    style = properties.find(f"{_W}pStyle")
    if style is not None:
        style_id = style.get(f"{_W}val") or ""
        name = styles.get(style_id, style_id).strip()
        match = _HEADING_STYLE.match(name)
        if match:
            return int(match.group(1))
        if name == "title":
            return 1
    outline = properties.find(f"{_W}outlineLvl")
    if outline is not None:
        raw = outline.get(f"{_W}val")
        if raw is not None and raw.isdigit():
            # `outlineLvl` is zero-based; 0 is the top level.
            return min(int(raw) + 1, 6)
    return None


def _list_marker(
    properties: ElementTree.Element | None,
    numbering: dict[tuple[str, str], str],
    counters: dict[tuple[str, str], int],
) -> str | None:
    if properties is None:
        return None
    reference = properties.find(f"{_W}numPr")
    if reference is None:
        return None
    ilvl_node = reference.find(f"{_W}ilvl")
    num_id_node = reference.find(f"{_W}numId")
    ilvl = (ilvl_node.get(f"{_W}val") if ilvl_node is not None else None) or "0"
    num_id = (num_id_node.get(f"{_W}val") if num_id_node is not None else None) or "0"
    if num_id == "0":
        # The spec reserves numId 0 for "numbering removed".
        return None
    indent = "  " * int(ilvl) if ilvl.isdigit() else ""
    if numbering.get((num_id, ilvl), "decimal") == "bullet":
        return f"{indent}- "
    key = (num_id, ilvl)
    counters[key] = counters.get(key, 0) + 1
    return f"{indent}{counters[key]}. "


def _table(table: ElementTree.Element) -> str:
    rows: list[list[str]] = []
    for row in table.findall(f"{_W}tr"):
        cells = [
            " ".join(_run_text(p).strip() for p in cell.findall(f"{_W}p")).strip()
            for cell in row.findall(f"{_W}tc")
        ]
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    lines = [
        "| " + " | ".join(padded[0]) + " |",
        "| " + " | ".join(["---"] * width) + " |",
        *("| " + " | ".join(row) + " |" for row in padded[1:]),
    ]
    return "\n".join(lines)


def _run_text(paragraph: ElementTree.Element) -> str:
    """Concatenate a paragraph's runs, keeping tabs and hard breaks.

    Iterating the whole subtree rather than descending straight to ``w:t`` is
    what includes text inside ``w:hyperlink``. Reaching for runs directly would
    silently delete every link's anchor text from the index.
    """
    parts: list[str] = []
    for node in paragraph.iter():
        if node.tag == f"{_W}t":
            parts.append(node.text or "")
        elif node.tag == f"{_W}tab":
            parts.append("\t")
        elif node.tag in (f"{_W}br", f"{_W}cr"):
            parts.append("\n")
    return "".join(parts)
