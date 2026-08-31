from __future__ import annotations

import re

from app.schemas.chat import CitationRef

_CITATION_PATTERN = re.compile(r"(?<!\[)\[(S[1-9]\d*)\](?!\])")
_URL_PREFIXES = ("http://", "https://")
_URL_TERMINATORS = frozenset("，。！？、；;）)]}>,'\"‘’“”")
_URL_TRAILING_CANDIDATES = frozenset(".:")


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
        self._inside_url = False
        self._trailing_candidate = ""

    def feed(self, text: str) -> str:
        output: list[str] = []
        for character in text:
            consumed_as_terminator = False
            while self._inside_url:
                if self._trailing_candidate:
                    if _continues_url_after(self._trailing_candidate, character):
                        self._trailing_candidate = ""
                    else:
                        output.append(self._trailing_candidate)
                        self._trailing_candidate = ""
                        self._inside_url = False
                        continue
                if character in _URL_TRAILING_CANDIDATES:
                    self._trailing_candidate = character
                elif character.isspace() or character in _URL_TERMINATORS:
                    self._inside_url = False
                    output.append(character)
                    consumed_as_terminator = True
                break
            if self._inside_url or consumed_as_terminator:
                continue

            self._pending += character
            while self._pending:
                normalized_pending = self._pending.lower()
                if normalized_pending in _URL_PREFIXES:
                    self._pending = ""
                    self._inside_url = True
                    break
                if any(prefix.startswith(normalized_pending) for prefix in _URL_PREFIXES):
                    break
                output.append(self._pending[0])
                self._pending = self._pending[1:]
        return "".join(output)

    def finish(self) -> str:
        if self._inside_url:
            self._inside_url = False
            self._pending = ""
            trailing, self._trailing_candidate = self._trailing_candidate, ""
            return trailing
        pending, self._pending = self._pending, ""
        return pending


def strip_model_urls(answer: str) -> str:
    sanitizer = URLStreamSanitizer()
    return sanitizer.feed(answer) + sanitizer.finish()


def _continues_url_after(candidate: str, character: str) -> bool:
    if candidate == ":":
        return character.isascii() and character.isdigit()
    return character.isascii() and (character.isalnum() or character in "_-/")
