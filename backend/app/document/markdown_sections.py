"""Splitting markdown into heading-delimited sections.

Lives on its own because more than one format produces markdown and every one of
them needs the same split: the PDF, HTML, Markdown and Word parsers all end up
here. It used to be a private static method on the parser, which meant any
out-of-tree format had to re-implement it — and the section path it builds is
what citations are keyed on, so a divergent copy would be a correctness bug, not
a style difference.
"""
from __future__ import annotations

import re

from app.schemas.imports import ParsedSection

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")

#: Fence markers. A ``#`` inside a fenced code block is a comment, not a
#: heading, so the split has to track them.
_FENCES = ("```", "~~~")


def sections_from_markdown(markdown: str) -> list[ParsedSection]:
    """Split markdown on ATX headings, tracking the path each block sits under.

    A block's ``heading_path`` is the chain of enclosing headings, not just its
    own. Skipped levels are honoured as written (``# a`` then ``### c`` yields
    ``["a", "c"]``) rather than being normalised, because that is what reopens
    the document at the right place.
    """
    sections: list[ParsedSection] = []
    path: list[str] = []
    current: list[str] = []
    current_path: list[str] = []
    in_fence = False

    for line in markdown.splitlines():
        if line.startswith(_FENCES):
            in_fence = not in_fence
        heading = None if in_fence else _HEADING.match(line)
        if heading:
            if current and "\n".join(current).strip():
                sections.append(ParsedSection(heading_path=current_path, markdown="\n".join(current).strip()))
            level = len(heading.group(1))
            text = heading.group(2).strip().rstrip("#").strip()
            path = path[: level - 1] + [text]
            current_path = list(path)
            current = [line]
        else:
            current.append(line)

    if current and "\n".join(current).strip():
        sections.append(ParsedSection(heading_path=current_path, markdown="\n".join(current).strip()))
    return sections


def title_from_markdown(markdown: str) -> str | None:
    """The first level-1 heading, stripped of its marks.

    Used as the document title when the source itself carries none — a Markdown
    file's own ``# Title`` is the title, and preferring it over the filename is
    what keeps `<uuid>.md` out of the index.
    """
    in_fence = False
    for line in markdown.splitlines():
        if line.startswith(_FENCES):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING.match(line)
        if match and len(match.group(1)) == 1:
            return match.group(2).strip().rstrip("#").strip()
    return None
