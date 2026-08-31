from __future__ import annotations

import re
from typing import Literal

from app.schemas.chat import CitationRef

_CITATION_PATTERN = re.compile(r"(?<!\[)\[(S[1-9]\d*)\](?!\])")
_URL_PREFIXES = ("http://", "https://")
_URL_TERMINATORS = frozenset("，。！？、；;）)]}>,'\"‘’“”")

_URLState = Literal["text", "scheme", "authority", "path"]


def parse_citations(answer: str, sources: dict[str, CitationRef]) -> list[CitationRef]:
    citations: list[CitationRef] = []
    seen: set[str] = set()
    for match in _CITATION_PATTERN.finditer(answer):
        source_id = match.group(1)
        source = sources.get(source_id)
        if source is None or source_id in seen:
            continue
        seen.add(source_id)
        citations.append(source)
    return citations


class URLStreamSanitizer:
    """Remove model-generated HTTP URLs even when a URL spans provider chunks."""

    def __init__(self) -> None:
        self._pending = ""
        self._state: _URLState = "text"
        self._trailing_candidate = ""

    def feed(self, text: str) -> str:
        output: list[str] = []
        for character in text:
            if self._state in {"authority", "path"} and self._consume_url_character(
                character, output
            ):
                continue
            self._consume_text_character(character, output)
        return "".join(output)

    def finish(self) -> str:
        if self._state in {"authority", "path"}:
            self._state = "text"
            self._pending = ""
            trailing, self._trailing_candidate = self._trailing_candidate, ""
            return trailing
        self._state = "text"
        pending, self._pending = self._pending, ""
        return pending

    def _consume_text_character(self, character: str, output: list[str]) -> None:
        self._pending += character
        while self._pending:
            normalized_pending = self._pending.lower()
            if normalized_pending in _URL_PREFIXES:
                self._pending = ""
                self._state = "authority"
                return
            if any(prefix.startswith(normalized_pending) for prefix in _URL_PREFIXES):
                self._state = "scheme"
                return
            output.append(self._pending[0])
            self._pending = self._pending[1:]
            self._state = "text"

    def _consume_url_character(self, character: str, output: list[str]) -> bool:
        while self._state in {"authority", "path"}:
            if self._trailing_candidate:
                if self._continues_after_candidate(character):
                    self._trailing_candidate = ""
                else:
                    output.append(self._trailing_candidate)
                    self._trailing_candidate = ""
                    self._state = "text"
                    return False

            if character.isspace() or character in _URL_TERMINATORS:
                self._state = "text"
                output.append(character)
                return True
            if self._state == "authority":
                if character in "/?#":
                    self._state = "path"
                elif character in ".:":
                    self._trailing_candidate = character
                return True
            if character == ":":
                self._state = "text"
                output.append(character)
            elif character == ".":
                self._trailing_candidate = character
            return True
        return False

    def _continues_after_candidate(self, character: str) -> bool:
        if self._state == "authority":
            if self._trailing_candidate == ":":
                return character.isascii() and character.isdigit()
            return character.isascii() and (
                character.isalnum() or character == "-"
            )
        return character.isascii() and (
            character.islower() or character.isdigit()
        )


def strip_model_urls(answer: str) -> str:
    sanitizer = URLStreamSanitizer()
    return sanitizer.feed(answer) + sanitizer.finish()
