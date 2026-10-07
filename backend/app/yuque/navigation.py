"""Stack-neutral Yuque navigation helpers.

The login probe, the read fallback and the write path all need to know the
same three things: did the page finish rendering, is this a login URL, and
what is the resource id. Those answers must not depend on which automation
stack produced the page, so they live here and both gateways import them.

``_wait_for_render`` in particular used to catch Playwright's own error. It now
asks :func:`app.yuque.retryable.is_retryable` instead, which covers both
stacks — a missing driver must not be importable just to describe "the page
did not load in time".
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlparse

from app.remote.markers import extract_mutation_marker, strip_mutation_marker
from app.yuque.retryable import is_retryable


async def _wait_for_render(page: Any, timeout_ms: int) -> bool:
    """True when the page actually reached ``domcontentloaded``.

    A rendered page that still lacks every login marker means the Yuque DOM moved
    and is worth surfacing; a page that never finished loading is a network
    problem and must not be reported as a structure change.
    """
    waiter = getattr(page, "wait_for_load_state", None)
    if waiter is None:
        return False
    try:
        await waiter("domcontentloaded", timeout=timeout_ms)
    except Exception as error:
        if not is_retryable(error):
            raise
        return False
    return True
async def _open_yuque_resource(page: Any, resource_id: str) -> None:
    url = resource_id if resource_id.startswith("https://www.yuque.com/") else (
        f"https://www.yuque.com/{quote(resource_id.strip('/'), safe='/')}"
    )
    await page.goto(url, wait_until="domcontentloaded")
def _is_login_url(url: str) -> bool:
    return urlparse(url).path.rstrip("/") == "/login"
def _repository_id_from_document_url(url: str) -> str:
    path = urlparse(url).path.strip("/")
    repository_id, separator, _ = path.rpartition("/")
    return repository_id if separator else ""
def _resource_identity(value: str | None) -> str:
    if not value:
        return ""
    parsed = urlparse(value)
    return (parsed.path or value).strip("/")
_strip_mutation_marker = strip_mutation_marker
_extract_mutation_marker = extract_mutation_marker

__all__ = [
    "_extract_mutation_marker",
    "_is_login_url",
    "_open_yuque_resource",
    "_repository_id_from_document_url",
    "_resource_identity",
    "_strip_mutation_marker",
    "_wait_for_render",
]
