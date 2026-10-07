"""LaTeX source to markdown, using nothing but the standard library.

Why hand-written rather than ``pylatexenc``: DocMind ships no installer, so a
plugin that pulled a dependency in would only work on a machine that already had
one. The first real format plugin has to be the easy case — standard library
only — or "install a plugin by cloning it" is not actually true.

Scope is decided by what real documents use, not by what LaTeX can express. The
reference corpus (machine-learning and compiler-course reports, mixed Chinese and
English) contains ``section``/``subsection``, ``table``+``tabular`` with
booktabs rules, ``itemize``/``enumerate``, ``lstlisting``, ``figure`` with
``tikzpicture``, ``align*``, and a great deal of inline and display math.

Everything here is a single left-to-right scan rather than a cascade of
substitutions, because the same bytes mean different things in different places:
``\\\\`` is a row break inside a table and a line break outside one, ``&`` is a
column separator only at brace depth zero, and whether ``\\item`` is a bullet
depends on the environment. So structure is read before the text inside it is
converted.

What is kept, and what that means for retrieval:

* **Headings become ATX headings**, so the section path a citation is keyed on is
  the document's own structure rather than a flat page.
* **Math is preserved verbatim**, delimiters aside: ``\\frac``, ``\\mathcal`` and
  friends carry the meaning, and a symbol table that guessed at them would lose
  more than it gained.
* **Tables become GFM tables**, so a row can be found without its neighbours.
* **Captions are kept as lines of their own**, immediately before what they
  describe — that is the text a reader would search for.
* **Code blocks keep their bytes**, fences widened rather than escaped, because
  the point of a verbatim environment is that its content is literal.

What is dropped, deliberately: figures and ``tikzpicture`` (a reference to a path
that was never imported is noise for retrieval), font and colour commands
(``\\large``, ``\\color``), layout commands (``\\vspace``, ``\\clearpage``), and
``\\label``. Cross-references are not resolved — ``\\ref{tab:confusion}`` becomes
``[tab:confusion]``, which keeps the document traceable without inventing an
answer the source does not give. A ``\\multicolumn`` spanning three columns is
emitted as one cell holding its text, so a merged header reads as one value
rather than three.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: Heading command to ATX level. ``\\part`` is the outermost thing an article can
#: have and ``\\paragraph`` the innermost, and they land on levels 1 and 6 so the
#: whole hierarchy fits markdown's range without renumbering. Levels are NOT
#: normalised per document: a section is level 3 in every file, which is what
#: makes two documents' section paths comparable.
_HEADING_LEVELS = {
    "part": 1,
    "chapter": 2,
    "section": 3,
    "subsection": 4,
    "subsubsection": 5,
    "paragraph": 6,
}

_HEADING_PATTERN = re.compile(
    r"\\(part|chapter|section|subsection|subsubsection|paragraph)\s*(\*)?\s*(\[[^\]]*\])?\s*\{"
)

#: Environments whose contents are literal.
_CODE_ENVIRONMENTS = frozenset({"verbatim", "Verbatim", "lstlisting", "minted"})

#: Environments that are mathematics.
_MATH_ENVIRONMENTS = frozenset(
    {
        "equation",
        "equation*",
        "align",
        "align*",
        "aligned",
        "gather",
        "gather*",
        "multline",
        "multline*",
        "eqnarray",
        "eqnarray*",
        "displaymath",
    }
)

_LIST_ENVIRONMENTS = frozenset({"itemize", "enumerate", "description"})

#: Environments that only group content we render ourselves.
_TRANSPARENT_ENVIRONMENTS = frozenset(
    {
        "center",
        "flushleft",
        "flushright",
        "quote",
        "quotation",
        "sloppypar",
        "small",
        "document",
    }
)

#: Environments that render something markdown cannot express. Their source is
#: dropped rather than kept: it is LaTeX, and it would sit in the index as noise.
_OPAQUE_ENVIRONMENTS = frozenset(
    {"tikzpicture", "pgfpicture", "pspicture", "thebibliography", "comment"}
)

#: Commands that render nothing in text.
_SILENT = frozenset(
    {
        "noindent",
        "indent",
        "centering",
        "raggedright",
        "raggedleft",
        "clearpage",
        "cleardoublepage",
        "newpage",
        "pagebreak",
        "linebreak",
        "nopagebreak",
        "maketitle",
        "tableofcontents",
        "listoffigures",
        "listoftables",
        "bigskip",
        "medskip",
        "smallskip",
        "par",
        "today",
        "hfill",
        "vfill",
        "relax",
        "ignorespaces",
        "leavevmode",
        "protect",
        "hline",
        "toprule",
        "midrule",
        "bottomrule",
        "bfseries",
        "mdseries",
        "itshape",
        "slshape",
        "scshape",
        "upshape",
        "ttfamily",
        "rmfamily",
        "sffamily",
        "normalfont",
        "em",
        "displaystyle",
        "textstyle",
        "scriptstyle",
        "limits",
        "nolimits",
        "small",
        "footnotesize",
        "scriptsize",
        "tiny",
        "large",
        "Large",
        "LARGE",
        "huge",
        "Huge",
        "normalsize",
    }
)

#: Commands that render as whitespace of some width.
_SPACING = {
    "quad": "  ",
    "qquad": "    ",
    "enspace": " ",
    "enskip": " ",
    "thinspace": " ",
    "negthinspace": "",
    "space": " ",
    "hphantom": "",
    "vphantom": "",
}

#: One-argument commands whose argument is the content, wrapped in markdown.
_WRAPPERS = {
    "textbf": ("**", "**"),
    "bf": ("**", "**"),
    "emph": ("*", "*"),
    "textit": ("*", "*"),
    "it": ("*", "*"),
    "texttt": ("`", "`"),
    "tt": ("`", "`"),
    "textsc": ("", ""),
    "textsf": ("", ""),
    "textrm": ("", ""),
    "textnormal": ("", ""),
    "textup": ("", ""),
    "mbox": ("", ""),
    "text": ("", ""),
    "ensuremath": ("", ""),
    "underline": ("", ""),
    "uline": ("", ""),
    "thanks": ("", ""),
    "footnote": ("（", "）"),
    "caption": ("**", "**"),
}

#: ``command -> argument count``; the **last** argument is the content and the
#: rest are layout. ``\\multirow{2}{*}{text}`` and ``\\textcolor{red}{text}`` are
#: the shapes this exists for — a merged cell's text is content, its span is not.
_KEEP_LAST_ARGUMENT = frozenset({"multirow", "multicolumn", "textcolor", "texorpdfstring"})

#: ``command -> argument count``; every argument is dropped, along with any
#: optional ``[...]`` or ``(...)`` in front (``\\cmidrule(lr){2-4}``).
_DROPPED_COMMANDS = {
    "label": 1,
    "index": 1,
    "vspace": 1,
    "hspace": 1,
    "vskip": 1,
    "hskip": 1,
    "rule": 2,
    "cline": 1,
    "cmidrule": 1,
    "includegraphics": 1,
    "color": 1,
    "pagecolor": 1,
    "setlength": 2,
    "addtolength": 2,
    "renewcommand": 2,
    "newcommand": 2,
    "fontsize": 2,
    "numberwithin": 2,
    "documentclass": 1,
    "usepackage": 1,
    "geometry": 1,
    "setmainfont": 1,
    "setmonofont": 1,
    "bibliography": 1,
    "bibliographystyle": 1,
    "titleformat": 1,
    "setlist": 1,
    "lstset": 1,
    "hypersetup": 1,
    "graphicspath": 1,
}

#: Commands that name a cross-reference. The key is kept in brackets: resolving it
#: would mean inventing the caption, and dropping it would lose the link.
_REFERENCES = frozenset({"ref", "eqref", "autoref", "cref", "Cref", "nameref", "pageref"})

#: Commands that print a literal single character. ``\\\\`` is a line break, which
#: is why it maps to a newline rather than to a backslash.
_ESCAPED_CHARACTERS = {
    "%": "%",
    "&": "&",
    "#": "#",
    "$": "$",
    "_": "_",
    "{": "{",
    "}": "}",
    "~": "~",
    "^": "^",
    "\\": "\n",
    " ": " ",
    ",": " ",
    ";": " ",
    ":": " ",
    "!": "",
    "/": "",
    "-": "",
    "=": "",
    "@": "",
}

_COMMAND = re.compile(r"\\([a-zA-Z]+)\s*")
_LETTER = re.compile(r"[A-Za-z]")

#: A paragraph break, in either of the two ways LaTeX writes one. ``\\par`` is
#: checked as a whole word so that ``\\paragraph`` is not read as one.
_PARAGRAPH_BREAK = re.compile(r"(?:\n[ \t]*\n|[ \t]*\\par\b[ \t]*)+")

#: A ``tabular`` column specification, as opposed to a first cell that happens to
#: start with a brace. Matched only when it contains alignment letters and no cell
#: separator, which a spec never does.
_COLUMN_SPEC = re.compile(r"^[@|lcrpXmhb(){}.\-\d\s a-zA-Z]*$")


@dataclass(frozen=True)
class Conversion:
    """What a LaTeX file turns into.

    ``title`` is ``None`` when the file declares no title and has no heading to
    fall back on — the caller decides what to use instead, rather than this module
    guessing at a filename.
    """

    title: str | None
    markdown: str


def convert(source: str) -> Conversion:
    """Convert one LaTeX document."""
    text = strip_comments(source)
    preamble, body = split_document(text)
    title = _metadata_title(preamble)
    markdown = "\n\n".join(block for block in _blocks(body) if block.strip())
    if title is None:
        title = _first_heading(markdown)
    return Conversion(title=title, markdown=markdown)


# -- comments and the document body ------------------------------------------


def strip_comments(text: str) -> str:
    """Remove ``%`` comments, keeping escaped percents.

    A comment runs to end of line, so this cannot be a plain substitution: ``\\%``
    is a literal percent sign, and ``\\\\`` is a line break whose second backslash
    must not be read as escaping whatever follows it.
    """
    out: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        character = text[index]
        if character == "\\" and index + 1 < length:
            out.append(character)
            out.append(text[index + 1])
            index += 2
            continue
        if character == "%":
            newline = text.find("\n", index)
            if newline == -1:
                break
            # Advance to the newline without consuming it, so paragraph splitting
            # and line-oriented reading still see where lines end.
            index = newline
            continue
        out.append(character)
        index += 1
    return "".join(out)


def split_document(text: str) -> tuple[str, str]:
    """Separate the preamble from the body.

    The preamble is mostly package and layout setup that means nothing to a
    reader — one sample here is forty lines of font declarations. It is still
    returned separately because the title lives in it.
    """
    marker = text.find(r"\begin{document}")
    if marker == -1:
        return "", text
    end = text.find(r"\end{document}", marker)
    body = text[marker + len(r"\begin{document}") : end if end != -1 else len(text)]
    return text[:marker], body


def _metadata_title(preamble: str) -> str | None:
    match = re.search(r"\\title\s*\{", preamble)
    if match is None:
        return None
    content, _ = _read_braced(preamble, match.end() - 1)
    title = _inline(content).strip()
    return title or None


def _first_heading(markdown: str) -> str | None:
    """The shallowest heading, as a fallback title.

    Better than the staged filename, which is a bare UUID: an assignment with no
    ``\\title`` still opens with ``\\section{一. 性能度量}``, and that is what a
    reader would call the document.
    """
    for line in markdown.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            return match.group(2).strip()
    return None


# -- block scanning ----------------------------------------------------------


def _blocks(text: str) -> list[str]:
    blocks: list[str] = []
    buffer: list[str] = []
    position = 0
    length = len(text)

    def flush() -> None:
        blocks.extend(_paragraphs("".join(buffer)))
        buffer.clear()

    while position < length:
        character = text[position]

        if text.startswith(r"\begin{", position):
            name, optional, body, end = _read_environment(text, position)
            if name is None:
                buffer.append(character)
                position += 1
                continue
            flush()
            blocks.extend(_environment_blocks(name, optional, body))
            position = end
            continue

        # ``\\`` is checked before ``\[`` on purpose: ``\\[0.8em]`` is a line break
        # with extra space, and reading its second backslash as display math would
        # swallow the rest of the document into a formula.
        if text.startswith("\\\\", position):
            cursor = position + 2
            if cursor < length and text[cursor] == "[":
                _, cursor = _read_bracket(text, cursor)
            buffer.append("\\\\")
            position = cursor
            continue

        if text.startswith(r"\[", position):
            end = text.find(r"\]", position + 2)
            body = text[position + 2 : end if end != -1 else length]
            flush()
            blocks.append(_display_math(body))
            position = (end + 2) if end != -1 else length
            continue

        if text.startswith("$$", position):
            end = text.find("$$", position + 2)
            body = text[position + 2 : end if end != -1 else length]
            flush()
            blocks.append(_display_math(body))
            position = (end + 2) if end != -1 else length
            continue

        heading = _HEADING_PATTERN.match(text, position)
        if heading:
            content, end = _read_braced(text, heading.end() - 1)
            flush()
            level = _HEADING_LEVELS[heading.group(1)]
            blocks.append(f"{'#' * level} {_inline(content).strip()}")
            position = end
            continue

        buffer.append(character)
        position += 1

    flush()
    return blocks


def _paragraphs(raw: str) -> list[str]:
    out: list[str] = []
    for chunk in _PARAGRAPH_BREAK.split(raw):
        text = _inline(chunk).strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
        if text:
            out.append(_escape_line_starts(text))
    return out


def _environment_blocks(name: str, optional: str | None, body: str) -> list[str]:
    if name in _CODE_ENVIRONMENTS:
        return [_code_block(optional, body)]
    if name in _MATH_ENVIRONMENTS:
        return [_display_math(body)]
    if name in _LIST_ENVIRONMENTS:
        return [_list_block(name, body)]
    if name == "tabular":
        return [_table_block(_without_column_spec(body))]
    if name == "table":
        return _table_environment(body)
    if name == "figure":
        return _figure_environment(body)
    if name in _TRANSPARENT_ENVIRONMENTS:
        return _blocks(body)
    if name in _OPAQUE_ENVIRONMENTS:
        return []
    # An unknown environment is more likely to hold text than to hold nothing, so
    # its body is parsed rather than dropped.
    return _blocks(body)


def _code_block(optional: str | None, body: str) -> str:
    language = _code_language(optional)
    # Content is literal, so a fence inside it has to be widened rather than
    # escaped — the whole point of a verbatim environment is that its bytes are
    # what they are.
    fence = "```"
    while re.search(rf"^{fence}", body, re.MULTILINE):
        fence += "`"
    return f"{fence}{language}\n{body.strip(chr(10))}\n{fence}"


def _code_language(optional: str | None) -> str:
    if not optional:
        return ""
    match = re.search(r"language\s*=\s*([A-Za-z0-9_+#-]+)", optional)
    if match:
        return match.group(1).lower()
    first = optional.strip().split(",", 1)[0].strip()
    return first.lower() if re.fullmatch(r"[A-Za-z0-9_+#-]+", first) else ""


def _display_math(body: str) -> str:
    cleaned = re.sub(r"\\label\s*\{[^{}]*\}", "", body).strip()
    return f"$$\n{cleaned}\n$$"


def _table_environment(body: str) -> list[str]:
    """A ``table`` float: its caption, then the tabular it wraps."""
    caption, remainder = _take_command(body, "caption")
    blocks: list[str] = []
    if caption is not None and caption.strip():
        # A caption immediately before its table is the text a reader searches
        # for, so it is kept as a line rather than folded into the table.
        blocks.append(f"**{_inline(caption).strip()}**")
    if re.search(r"\\begin\{tabular\}", remainder):
        blocks.extend(_blocks(remainder))
    return blocks


def _figure_environment(body: str) -> list[str]:
    """A figure: its caption only.

    The image cannot be carried over — a reference to a path that was never
    imported is noise for retrieval — but the caption is descriptive text that
    belongs in the index.
    """
    caption, _ = _take_command(body, "caption")
    if caption is None or not caption.strip():
        return []
    return [f"**{_inline(caption).strip()}**"]


def _take_command(text: str, name: str) -> tuple[str | None, str]:
    """Pull ``\\name{...}`` out of ``text``, brace-aware.

    A regex cannot do this: a caption routinely contains braces of its own
    (``\\caption{学习器 $\\mathcal{L}$ 的矩阵}``), and matching up to the first
    ``}`` leaves the tail of the caption behind as body text.
    """
    match = re.search(r"\\" + name + r"\s*(?:\[[^\]]*\])?\s*\{", text)
    if match is None:
        return None, text
    content, end = _read_braced(text, match.end() - 1)
    return content, text[: match.start()] + text[end:]


def _without_column_spec(body: str) -> str:
    """Drop a ``tabular``'s ``{lccc}`` preamble.

    It is an argument of the environment rather than a cell, but it sits inside
    the body, so leaving it in makes it the table's first row.
    """
    stripped = body.lstrip()
    if not stripped.startswith("{"):
        return body
    content, end = _read_braced(stripped, 0)
    if "&" in content or "\\\\" in content or not _COLUMN_SPEC.match(content):
        return body
    if not any(letter in content for letter in "lcrpXmhb"):
        return body
    return stripped[end:]


def _table_block(body: str) -> str:
    """Render a ``tabular`` body as a GFM table."""
    rows: list[list[str]] = []
    for raw_row in _split_top_level(body, "\\\\"):
        cells = [_inline(cell).strip() for cell in _split_top_level(raw_row, "&")]
        # A row holding only rules and spacing, or the empty tail left by the last
        # ``\\``, carries nothing.
        if not any(cell for cell in cells):
            continue
        rows.append(cells)
    if not rows:
        return ""

    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    header, *rest = rows
    lines = [
        "| " + " | ".join(_escape_cell(cell) for cell in header) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    lines.extend("| " + " | ".join(_escape_cell(cell) for cell in row) + " |" for row in rest)
    return "\n".join(lines)


def _escape_cell(cell: str) -> str:
    # A newline ends the row in GFM, and LaTeX wraps cell text freely, so the
    # line breaks a source carries are not breaks in the table.
    flattened = " ".join(cell.split())
    # A ``|`` inside a cell would split the row.
    return flattened.replace("|", "\\|")


def _list_block(name: str, body: str) -> str:
    """Render a list environment, nesting included."""
    lines: list[str] = []
    for index, (label, content) in enumerate(_split_items(body), start=1):
        nested, own = _separate_nested(content)
        blocks = [block for block in _blocks(own) if block.strip()]
        marker = "-" if name != "enumerate" else f"{index}."

        if blocks and not _is_block_level(blocks[0]):
            head = " ".join(blocks[0].split())
            if label:
                # ``description`` puts its term in the optional argument;
                # ``itemize`` sometimes does too, and either way it belongs in
                # front of the item's text.
                head = f"**{_inline(label).strip()}** {head}".strip()
            lines.append(f"{marker} {head}".rstrip())
            tail = blocks[1:]
        else:
            # A formula, a table or a code fence cannot share the marker's line.
            if label:
                lines.append(f"{marker} **{_inline(label).strip()}**")
            else:
                lines.append(marker)
            tail = blocks

        for block in [*tail, *[b for b in _blocks(nested) if b.strip()]]:
            lines.extend("  " + line for line in block.splitlines())
    return "\n".join(lines)


def _is_block_level(block: str) -> bool:
    """Whether a block must start on its own line.

    Markdown has no way to put a formula, a table or a code fence mid-sentence,
    so these keep the list marker to themselves.
    """
    return block.startswith(("$$", "|", "```", "~~~"))


def _split_items(body: str) -> list[tuple[str | None, str]]:
    """Split a list body on its top-level ``\\item`` commands."""
    items: list[tuple[str | None, str]] = []
    label: str | None = None
    current: list[str] = []
    started = False
    position = 0
    depth = 0
    while position < len(body):
        if body.startswith(r"\begin{", position):
            _, _, _, end = _read_environment(body, position)
            if end == position:
                current.append(body[position])
                position += 1
                continue
            depth += 1
            current.append(body[position:end])
            position = end
            continue
        if body.startswith(r"\end{", position):
            depth = max(0, depth - 1)
        if depth == 0 and body.startswith(r"\item", position):
            after = position + len(r"\item")
            if after >= len(body) or not _LETTER.match(body[after]):
                if started:
                    items.append((label, "".join(current)))
                current, label, started = [], None, True
                if after < len(body) and body[after] == "[":
                    label, after = _read_bracket(body, after)
                position = after
                continue
        current.append(body[position])
        position += 1
    if started:
        items.append((label, "".join(current)))
    return [(term, text) for term, text in items if text.strip() or term]


def _separate_nested(content: str) -> tuple[str, str]:
    """Split an item's content into its nested environments and its own text.

    Nested lists have to be rendered after the item they belong to, so they are
    pulled out rather than left in reading order. The slices are the original
    bytes, not a reconstruction, so nothing is lost on the way through.
    """
    nested: list[str] = []
    own: list[str] = []
    position = 0
    while position < len(content):
        if content.startswith(r"\begin{", position):
            name, _, _, end = _read_environment(content, position)
            if name is not None and name in _LIST_ENVIRONMENTS:
                nested.append(content[position:end])
                position = end
                continue
            if name is None:
                own.append(content[position])
                position += 1
                continue
        own.append(content[position])
        position += 1
    return "".join(nested), "".join(own)


# -- structural reading ------------------------------------------------------


def _read_environment(text: str, position: int) -> tuple[str | None, str | None, str, int]:
    """Read ``\\begin{name}`` through its matching ``\\end{name}``.

    Nesting of the *same* environment is counted, so an ``itemize`` inside an
    ``itemize`` does not end its parent early. Returns ``position`` unchanged in
    the name slot when the environment is not properly closed, which callers
    treat as ordinary text.
    """
    open_match = re.compile(r"\\begin\s*\{([^{}]*)\}").match(text, position)
    if open_match is None:
        return None, None, "", position
    name = open_match.group(1).strip()
    cursor = open_match.end()
    optional: str | None = None
    if cursor < len(text) and text[cursor] == "[":
        optional, cursor = _read_bracket(text, cursor)

    end_pattern = re.compile(r"\\(begin|end)\s*\{" + re.escape(name) + r"\}")
    depth = 1
    body_start = cursor
    while True:
        match = end_pattern.search(text, cursor)
        if match is None:
            return None, None, "", position
        if match.group(1) == "begin":
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return name, optional, text[body_start : match.start()], match.end()
        cursor = match.end()


def _read_braced(text: str, position: int) -> tuple[str, int]:
    """Read a ``{...}`` group. Returns ``position`` unchanged when unclosed."""
    if position >= len(text) or text[position] != "{":
        return "", position
    depth = 0
    cursor = position
    while cursor < len(text):
        character = text[cursor]
        if character == "\\":
            cursor += 2
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[position + 1 : cursor], cursor + 1
        cursor += 1
    return text[position + 1 :], len(text)


def _read_bracket(text: str, position: int) -> tuple[str | None, int]:
    if position >= len(text) or text[position] != "[":
        return None, position
    depth = 0
    cursor = position
    while cursor < len(text):
        character = text[cursor]
        if character == "\\":
            cursor += 2
            continue
        if character == "[":
            depth += 1
        elif character == "]":
            depth -= 1
            if depth == 0:
                return text[position + 1 : cursor], cursor + 1
        cursor += 1
    return text[position + 1 :], len(text)


def _skip_prelude(text: str, position: int) -> int:
    """Step over an optional ``[...]`` or ``(...)`` group."""
    while position < len(text) and text[position] in "[(":
        closing = "]" if text[position] == "[" else ")"
        end = text.find(closing, position)
        if end == -1:
            return position
        position = end + 1
    return position


def _split_top_level(text: str, separator: str) -> list[str]:
    """Split on ``separator`` at brace depth zero and outside environments.

    ``\\\\`` inside a nested ``tabular`` is that table's row break, and ``&``
    inside a ``\\multicolumn`` argument is not a column separator. Depth is
    tracked with a backslash-aware scan, so ``\\{`` is a literal brace and never
    changes the count.
    """
    parts: list[str] = []
    current: list[str] = []
    position = 0
    depth = 0
    environment = 0
    while position < len(text):
        if depth == 0 and environment == 0 and text.startswith(separator, position):
            parts.append("".join(current))
            current = []
            position += len(separator)
            continue
        if text.startswith(r"\begin{", position):
            environment += 1
        elif text.startswith(r"\end{", position):
            environment = max(0, environment - 1)
        character = text[position]
        if character == "\\" and position + 1 < len(text):
            current.append(character)
            current.append(text[position + 1])
            position += 2
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth = max(0, depth - 1)
        current.append(character)
        position += 1
    parts.append("".join(current))
    return parts


# -- inline conversion -------------------------------------------------------


def _inline(text: str) -> str:
    """Convert the commands inside one run of text.

    Math is lifted out first and restored last, so ``\\frac`` is never mistaken
    for an unknown command and ``\\,`` is never mistaken for a spacing command.
    """
    protected: list[str] = []
    text = _protect_math(text, protected)

    text = text.replace("~", " ")
    text = _unwrap_known_commands(text)
    text = _drop_known_commands(text)
    text = _resolve_references(text)
    text = _unescape(text)
    # LaTeX braces only group, so they never render. Stripping them here is also
    # what lets the unknown-command pass below work on arguments it can no longer
    # see as arguments — the text is the same either way.
    text = text.replace("{", "").replace("}", "")
    text = _drop_unknown_commands(text)

    for index, original in enumerate(protected):
        text = text.replace(f"\x00{index}\x00", original)
    return text


def _protect_math(text: str, protected: list[str]) -> str:
    out: list[str] = []
    position = 0
    while position < len(text):
        character = text[position]
        if character == "\\" and position + 1 < len(text) and text[position + 1] == "(":
            end = text.find(r"\)", position + 2)
            if end != -1:
                protected.append(text[position : end + 2])
                out.append(f"\x00{len(protected) - 1}\x00")
                position = end + 2
                continue
        if character == "$":
            delimiter = "$$" if text.startswith("$$", position) else "$"
            end = text.find(delimiter, position + len(delimiter))
            if end != -1:
                protected.append(text[position : end + len(delimiter)])
                out.append(f"\x00{len(protected) - 1}\x00")
                position = end + len(delimiter)
                continue
        out.append(character)
        position += 1
    return "".join(out)


def _unwrap_known_commands(text: str) -> str:
    out: list[str] = []
    position = 0
    while position < len(text):
        match = _COMMAND.match(text, position)
        if match and match.group(1) in _WRAPPERS:
            name = match.group(1)
            cursor = match.end()
            if cursor < len(text) and text[cursor] == "{":
                content, end = _read_braced(text, cursor)
                opened, closed = _WRAPPERS[name]
                # The argument is converted in its own right: ``\textbf{\large x}``
                # nests, and skipping the argument would leave ``\large`` in the
                # output as if it were text.
                inner = _inline(content)
                if opened:
                    # ``** text **`` is not emphasis in markdown, it is asterisks.
                    # Padding arrives from things like ``\textbf{\color{blue} 姓名}``.
                    inner = inner.strip()
                if inner:
                    out.append(f"{opened}{inner}{closed}")
                position = end
                continue
        out.append(text[position])
        position += 1
    return "".join(out)


def _drop_known_commands(text: str) -> str:
    out: list[str] = []
    position = 0
    while position < len(text):
        match = _COMMAND.match(text, position)
        if match:
            name = match.group(1)
            cursor = _skip_prelude(text, match.end())

            if name in _KEEP_LAST_ARGUMENT:
                # ``\multirow{2}{*}{真实类别}``: the text is content, the span is
                # layout. The leading arguments are skipped rather than emitted,
                # or the column span would read as a number.
                arguments, end = _read_arguments(text, cursor, _ARITY[name])
                if arguments:
                    out.append(arguments[-1])
                    position = end
                    continue

            if name in _DROPPED_COMMANDS:
                _, cursor = _read_arguments(text, cursor, _DROPPED_COMMANDS[name])
                position = cursor
                continue

            if name in _SPACING:
                _, cursor = _read_arguments(text, cursor, 1)
                out.append(_SPACING[name])
                position = cursor
                continue

            if name == "href":
                arguments, end = _read_arguments(text, cursor, 2)
                if len(arguments) == 2:
                    out.append(f"[{arguments[0]}]({arguments[1]})")
                    position = end
                    continue

            if name == "url":
                arguments, end = _read_arguments(text, cursor, 1)
                if arguments:
                    out.append(arguments[0])
                    position = end
                    continue

            if name in _SILENT:
                position = cursor
                continue
        out.append(text[position])
        position += 1
    return "".join(out)


#: How many arguments the kept-first commands take, so the extras can be skipped.
_ARITY = {"multirow": 3, "multicolumn": 3, "textcolor": 2, "texorpdfstring": 2}


def _read_arguments(text: str, position: int, count: int) -> tuple[list[str], int]:
    """Read up to ``count`` braced arguments, stopping at the first one missing."""
    arguments: list[str] = []
    cursor = position
    for _ in range(count):
        if cursor < len(text) and text[cursor] == "{":
            content, cursor = _read_braced(text, cursor)
            arguments.append(content)
        else:
            break
    return arguments, cursor


def _resolve_references(text: str) -> str:
    out: list[str] = []
    position = 0
    while position < len(text):
        match = _COMMAND.match(text, position)
        if match:
            name = match.group(1)
            if name in _REFERENCES or name.startswith("cite"):
                cursor = _skip_prelude(text, match.end())
                if cursor < len(text) and text[cursor] == "{":
                    key, end = _read_braced(text, cursor)
                    out.append(f"[{key}]")
                    position = end
                    continue
        out.append(text[position])
        position += 1
    return "".join(out)


def _unescape(text: str) -> str:
    out: list[str] = []
    position = 0
    while position < len(text):
        if text[position] == "\\" and position + 1 < len(text):
            following = text[position + 1]
            if following in _ESCAPED_CHARACTERS:
                out.append(_ESCAPED_CHARACTERS[following])
                position += 2
                continue
        out.append(text[position])
        position += 1
    return "".join(out)


def _drop_unknown_commands(text: str) -> str:
    """Last pass: keep what a command's argument says, drop the command.

    A macro with a braced argument is almost always a wrapper around text
    (``\\sectionname{...}``), so the argument is worth keeping. A bare unknown
    command is a font or layout switch that renders to nothing.
    """
    out: list[str] = []
    position = 0
    while position < len(text):
        match = _COMMAND.match(text, position)
        if match:
            cursor = match.end()
            if cursor < len(text) and text[cursor] == "{":
                content, end = _read_braced(text, cursor)
                out.append(_drop_unknown_commands(content))
                position = end
                continue
            position = cursor
            continue
        out.append(text[position])
        position += 1
    return "".join(out)


def _escape_line_starts(text: str) -> str:
    """Neutralise a ``#`` that would be read as a heading.

    Section splitting is driven by line-leading ``#``, so a paragraph that begins
    with one would silently invent a section. ``\\#`` is markdown's escape for a
    literal hash, so what a reader sees is unchanged.
    """
    return re.sub(r"(?m)^(#{1,6})(?=\s)", r"\\\1", text)
