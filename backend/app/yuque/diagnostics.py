from __future__ import annotations

import json
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit

_KEEP_ATTRS = {"data-testid", "role", "class"}


@dataclass(frozen=True)
class SelectorAttempt:
    selector: str
    matched: bool
    elapsed_ms: float


@dataclass(frozen=True)
class YuqueDiagnostic:
    operation: str
    step: str
    page_host: str
    candidate_selectors: tuple[str, ...]
    matched_selector: str | None
    attempts: tuple[SelectorAttempt, ...]
    error_code: str
    occurred_at: str


def redact_page_url(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme or 'https'}://{parts.hostname or ''}"


class _RedactingHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        kept = [(name, value) for name, value in attrs if name in _KEEP_ATTRS and value]
        self.parts.append(f"<{tag}{self._attrs(kept)}>")

    def handle_endtag(self, tag: str) -> None:
        self.parts.append(f"</{tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        kept = [(name, value) for name, value in attrs if name in _KEEP_ATTRS and value]
        self.parts.append(f"<{tag}{self._attrs(kept)}/>")

    @staticmethod
    def _attrs(attrs: list[tuple[str, str | None]]) -> str:
        return "".join(f' {name}="{value}"' for name, value in attrs)


def redact_dom_snapshot(html: str) -> str:
    parser = _RedactingHTMLParser()
    parser.feed(html)
    return "".join(parser.parts)


def diagnostic_json(diagnostic: YuqueDiagnostic) -> str:
    return json.dumps(
        {
            "operation": diagnostic.operation,
            "step": diagnostic.step,
            "page_host": diagnostic.page_host,
            "candidate_selectors": list(diagnostic.candidate_selectors),
            "matched_selector": diagnostic.matched_selector,
            "attempts": [
                {"selector": a.selector, "matched": a.matched, "elapsed_ms": a.elapsed_ms}
                for a in diagnostic.attempts
            ],
            "error_code": diagnostic.error_code,
            "occurred_at": diagnostic.occurred_at,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
