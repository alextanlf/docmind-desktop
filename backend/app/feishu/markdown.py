"""Best-effort Markdown reconstruction from Feishu docx blocks.

The Feishu ``raw_content`` endpoint deliberately discards structure.  The
block listing endpoint used here returns the page tree plus the rich text
payloads, so common document structures can be reconstructed without adding a
hand-written write model.
"""
from __future__ import annotations

from typing import Any

_BLOCK_TYPES = {
    1: "page",
    2: "text",
    3: "heading1",
    4: "heading2",
    5: "heading3",
    6: "heading4",
    7: "heading5",
    8: "heading6",
    9: "heading7",
    10: "heading8",
    11: "heading9",
    12: "bullet",
    13: "ordered",
    14: "code",
    15: "quote",
    16: "equation",
    17: "todo",
    19: "callout",
    22: "divider",
    23: "file",
    24: "grid",
    25: "grid_column",
    26: "iframe",
    27: "image",
    30: "sheet",
    31: "table",
    32: "table_cell",
    34: "quote_container",
    48: "link_preview",
}


def render_document_blocks(blocks: list[dict[str, Any]]) -> str:
    """Render a flat Feishu block list into Markdown.

    Feishu returns blocks as a flat list and expresses reading order through
    ``children`` / ``parent_id``.  Unknown blocks are skipped unless they have
    children, in which case their children are rendered in place.
    """
    items = [block for block in blocks if isinstance(block, dict) and block.get("block_id")]
    by_id = {str(block["block_id"]): block for block in items}
    explicit_children: dict[str, list[str]] = {}
    implicit_children: dict[str, list[str]] = {}
    for block in items:
        block_id = str(block["block_id"])
        children = block.get("children")
        if isinstance(children, list):
            explicit_children[block_id] = [
                str(child) for child in children if str(child) in by_id
            ]
        parent_id = block.get("parent_id")
        if parent_id is not None:
            implicit_children.setdefault(str(parent_id), []).append(block_id)

    def child_ids(block_id: str) -> list[str]:
        return explicit_children.get(block_id) or implicit_children.get(block_id, [])

    page = next((block for block in items if block.get("block_type") == 1), None)
    root_ids = child_ids(str(page["block_id"])) if page is not None else []
    if not root_ids:
        root_ids = [
            str(block["block_id"])
            for block in items
            if block is not page and str(block.get("parent_id") or "") not in by_id
        ]

    rendered = _render_sequence(root_ids, 0, by_id, child_ids)
    return rendered.strip()


def _render_sequence(
    block_ids: list[str],
    depth: int,
    by_id: dict[str, dict[str, Any]],
    child_ids,
) -> str:
    parts: list[str] = []
    for block_id in block_ids:
        block = by_id.get(block_id)
        if block is None:
            continue
        rendered = _render_block(block, depth, by_id, child_ids)
        if rendered.strip():
            parts.append(rendered.rstrip())
    pieces: list[str] = []
    for index, part in enumerate(parts):
        if index == 0:
            pieces.append(part)
            continue
        previous = parts[index - 1]
        separator = "\n" if _is_list_item(previous) and _is_list_item(part) else "\n\n"
        pieces.append(separator)
        pieces.append(part)
    return "".join(pieces)


def _render_block(
    block: dict[str, Any],
    depth: int,
    by_id: dict[str, dict[str, Any]],
    child_ids,
) -> str:
    block_type = block.get("block_type")
    name = _BLOCK_TYPES.get(block_type)
    block_id = str(block.get("block_id") or "")
    children = child_ids(block_id) if block_id else []

    if name == "page":
        return ""
    if name == "text":
        return _render_text(block.get("text"))
    if name and name.startswith("heading"):
        level = min(int(name.removeprefix("heading")), 6)
        content = _render_text(block.get(name))
        return f"{'#' * level} {content}".rstrip() if content else ""
    if name == "bullet":
        return _render_list_item(block.get("bullet"), depth, "- ", by_id, child_ids, block_id)
    if name == "ordered":
        return _render_list_item(block.get("ordered"), depth, "1. ", by_id, child_ids, block_id)
    if name == "todo":
        content = _render_text(block.get("todo"))
        checked = bool(((block.get("todo") or {}).get("style") or {}).get("done"))
        marker = "- [x] " if checked else "- [ ] "
        nested = _render_children(children, depth + 1, by_id, child_ids)
        return f"{marker}{content}" + (f"\n{nested}" if nested else "")
    if name == "code":
        return _render_code(block.get("code"))
    if name == "quote":
        content = _render_text(block.get("quote"))
        return _prefix_lines(content, "> ")
    if name == "equation":
        content = _render_text(block.get("equation"))
        return f"$$\n{content}\n$$" if content else ""
    if name == "divider":
        return "---"
    if name == "table":
        return _render_table(block, by_id, child_ids)
    if name == "callout":
        content = _render_children(children, depth, by_id, child_ids)
        return _prefix_lines(content, "> ")
    if name == "quote_container":
        content = _render_children(children, depth, by_id, child_ids)
        return _prefix_lines(content, "> ")
    if name in {"grid", "grid_column"}:
        return _render_children(children, depth, by_id, child_ids)
    if name == "image":
        image = block.get("image") or {}
        token = image.get("token") or block_id
        caption = str((image.get("caption") or {}).get("content") or "图片")
        return f"![{caption}](feishu://image/{token})"
    if name == "file":
        file_block = block.get("file") or {}
        token = file_block.get("token") or block_id
        label = str(file_block.get("name") or "附件")
        return f"[{label}](feishu://file/{token})"
    if name == "iframe":
        component = block.get("iframe") or {}
        component = component.get("component") or {}
        url = component.get("url")
        return f"[嵌入内容]({url})" if url else ""
    if name == "link_preview":
        preview = block.get("link_preview") or {}
        url = preview.get("url")
        return f"[{url}]({url})" if url else ""
    if name in {"sheet", "bitable", "mindnote", "diagram"}:
        return f"[嵌入内容](feishu://block/{block_id})"

    return _render_children(children, depth, by_id, child_ids)


def _render_children(
    block_ids: list[str],
    depth: int,
    by_id: dict[str, dict[str, Any]],
    child_ids,
) -> str:
    return _render_sequence(block_ids, depth, by_id, child_ids)


def _render_list_item(
    text: dict[str, Any] | None,
    depth: int,
    prefix: str,
    by_id: dict[str, dict[str, Any]],
    child_ids,
    block_id: str,
) -> str:
    content = _render_text(text)
    line = f"{'  ' * depth}{prefix}{content}".rstrip()
    children = child_ids(block_id)
    if not children:
        return line
    nested = _render_sequence(children, depth + 1, by_id, child_ids)
    return f"{line}\n{nested}" if nested else line


def _render_table(
    block: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    child_ids,
) -> str:
    table = block.get("table") or {}
    cell_ids = [str(cell) for cell in (table.get("cells") or [])]
    property_data = table.get("property") or {}
    try:
        column_size = max(int(property_data.get("column_size") or 0), 1)
    except (TypeError, ValueError):
        column_size = 1
    rows: list[list[str]] = []
    for offset in range(0, len(cell_ids), column_size):
        row_cells = cell_ids[offset : offset + column_size]
        row: list[str] = []
        for cell_id in row_cells:
            cell = by_id.get(cell_id)
            rendered = (
                _render_children(child_ids(cell_id), 0, by_id, child_ids)
                if cell is not None
                else ""
            )
            row.append(_escape_table_cell(rendered))
        if row:
            rows.append(row)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    header = normalized[0]
    body = normalized[1:]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def _render_text(text: Any) -> str:
    if not isinstance(text, dict):
        return ""
    return "".join(_render_text_element(element) for element in (text.get("elements") or []))


def _plain_text(text: Any) -> str:
    if not isinstance(text, dict):
        return ""
    parts: list[str] = []
    for element in text.get("elements") or []:
        if not isinstance(element, dict):
            continue
        if isinstance(element.get("text_run"), dict):
            parts.append(str(element["text_run"].get("content") or ""))
        elif isinstance(element.get("equation"), dict):
            parts.append(str(element["equation"].get("content") or ""))
        elif isinstance(element.get("mention_doc"), dict):
            mention = element["mention_doc"]
            parts.append(str(mention.get("title") or mention.get("url") or ""))
        elif isinstance(element.get("link_preview"), dict):
            preview = element["link_preview"]
            parts.append(str(preview.get("title") or preview.get("url") or ""))
    return "".join(parts)


def _render_text_element(element: Any) -> str:
    if not isinstance(element, dict):
        return ""
    if isinstance(element.get("text_run"), dict):
        return _render_text_run(element["text_run"])
    if isinstance(element.get("mention_doc"), dict):
        mention = element["mention_doc"]
        title = str(mention.get("title") or mention.get("token") or "")
        url = mention.get("url")
        return f"[{title}]({url})" if url else title
    if isinstance(element.get("mention_user"), dict):
        user_id = str(element["mention_user"].get("user_id") or "")
        return f"@{user_id}" if user_id else "@"
    if isinstance(element.get("equation"), dict):
        content = str(element["equation"].get("content") or "")
        return f"${content}$" if content else ""
    if isinstance(element.get("link_preview"), dict):
        preview = element["link_preview"]
        title = str(preview.get("title") or preview.get("url") or "")
        url = preview.get("url")
        return f"[{title}]({url})" if url else title
    if isinstance(element.get("file"), dict):
        token = element["file"].get("file_token") or ""
        return f"[附件](feishu://file/{token})" if token else ""
    if isinstance(element.get("reminder"), dict):
        return ""
    return ""


def _render_text_run(run: dict[str, Any]) -> str:
    content = str(run.get("content") or "")
    if not content:
        return ""
    style = run.get("text_element_style") or {}
    if style.get("inline_code"):
        return _inline_code(content)
    if style.get("bold"):
        content = f"**{content}**"
    if style.get("italic"):
        content = f"*{content}*"
    if style.get("strikethrough"):
        content = f"~~{content}~~"
    link = (style.get("link") or {}).get("url")
    if link:
        return f"[{content}]({link})"
    return content


def _render_code(text: Any) -> str:
    body = _plain_text(text).rstrip("\n")
    if not body:
        return ""
    fence = "```"
    while fence in body:
        fence += "`"
    return f"{fence}\n{body}\n{fence}"


def _inline_code(content: str) -> str:
    fence = "`"
    while fence in content:
        fence += "`"
    return f"{fence}{content}{fence}"


def _prefix_lines(content: str, prefix: str) -> str:
    if not content:
        return ""
    return "\n".join(f"{prefix}{line}" if line else prefix.rstrip() for line in content.splitlines())


def _escape_table_cell(content: str) -> str:
    return content.replace("|", r"\|").replace("\n", "<br>").strip()


def _is_list_item(content: str) -> bool:
    first = next((line for line in content.splitlines() if line.strip()), "")
    stripped = first.lstrip()
    return stripped.startswith(("- ", "1. "))
