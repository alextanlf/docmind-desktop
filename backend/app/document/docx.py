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

import re
import zipfile
from io import BytesIO
from xml.etree import ElementTree

from app.api.errors import DomainError
from app.document.markdown_sections import sections_from_markdown
from app.schemas.imports import DownloadedDocument, ParsedDocument

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_DC_TITLE = "{http://purl.org/dc/elements/1.1/}title"

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

    markdown = _convert_body(body_xml, styles, numbering)
    return ParsedDocument(
        title=core_title or document.title,
        markdown=markdown,
        source_url=document.source_url,
        sections=sections_from_markdown(markdown),
    )


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


def _convert_body(body_xml: bytes, styles: dict[str, str], numbering: dict[tuple[str, str], str]) -> str:
    try:
        root = ElementTree.fromstring(body_xml)
    except ElementTree.ParseError:
        raise DomainError("SOURCE_UNSUPPORTED", "Word 文件正文损坏", 400, False) from None
    body = root.find(f"{_W}body")
    if body is None:
        return ""
    blocks: list[str] = []
    _walk(body, styles, numbering, {}, blocks)
    return "\n\n".join(block for block in blocks if block.strip())


def _walk(
    parent: ElementTree.Element,
    styles: dict[str, str],
    numbering: dict[tuple[str, str], str],
    counters: dict[tuple[str, str], int],
    blocks: list[str],
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
        elif child.tag == f"{_W}tbl":
            table = _table(child)
            if table:
                blocks.append(table)
        elif len(child):
            _walk(child, styles, numbering, counters, blocks)


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
