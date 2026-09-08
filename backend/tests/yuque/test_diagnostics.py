from __future__ import annotations

from app.yuque.diagnostics import (
    SelectorAttempt,
    YuqueDiagnostic,
    diagnostic_json,
    redact_dom_snapshot,
    redact_page_url,
)


def test_redact_page_url_keeps_only_scheme_and_hostname() -> None:
    assert redact_page_url("https://www.yuque.com/team/repo/doc?x=1#y") == "https://www.yuque.com"


def test_redact_dom_snapshot_keeps_structure_and_drops_text_and_links() -> None:
    html = (
        '<html><body><div data-testid="doc" class="card">secret body'
        '<a href="https://yuque.com/team/doc">title</a></div></body></html>'
    )
    redacted = redact_dom_snapshot(html)
    assert "secret body" not in redacted
    assert "title" not in redacted
    assert "href" not in redacted
    assert 'data-testid="doc"' in redacted
    assert 'class="card"' in redacted


def test_diagnostic_json_serializes_without_secrets() -> None:
    diagnostic = YuqueDiagnostic(
        operation="create-document",
        step="import-markdown",
        page_host="https://www.yuque.com",
        candidate_selectors=("testid=save",),
        matched_selector=None,
        attempts=(SelectorAttempt("testid=save", False, 1.5),),
        error_code="YUQUE_PAGE_CHANGED",
        occurred_at="2026-09-08T00:00:00+00:00",
    )
    payload = diagnostic_json(diagnostic)
    assert "create-document" in payload
    assert "import-markdown" in payload
    assert "testid=save" in payload
