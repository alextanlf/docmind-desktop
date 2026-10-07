"""Errors the page layer treats as "the element/DOM is not ready yet".

``BasePage.with_retry`` and ``wait_for_any`` are written against this tuple so
a selector that has not appeared yet is retried instead of failing the whole
operation. Both browser stacks therefore share one page implementation:

* Playwright raises ``PlaywrightError``;
* WebDriver raises Selenium's ``WebDriverException`` family, whose transient
  members (missing element, stale reference, not interactable) are already
  narrowed down in :mod:`app.yuque.wd_locator`.

Keeping the tuple here — rather than in either stack's module — is what lets
``base_page`` stop importing a concrete automation library. A missing driver
must not be importable just to describe "not ready".
"""
from __future__ import annotations

from app.yuque.wd_locator import _RETRYABLE as _WEBDRIVER_RETRYABLE

_RETRYABLE_ERRORS: tuple[type[BaseException], ...] = (
    *_WEBDRIVER_RETRYABLE,
    TimeoutError,
    ConnectionError,
    OSError,
)


def is_retryable(error: BaseException) -> bool:
    """Whether ``error`` means "not ready yet" rather than "broken"."""
    return isinstance(error, _RETRYABLE_ERRORS)


__all__ = ["_RETRYABLE_ERRORS", "is_retryable"]
