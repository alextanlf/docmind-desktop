"""Errors the page layer treats as "the element/DOM is not ready yet".

``BasePage.with_retry`` and ``wait_for_any`` are written against this tuple so
a selector that has not appeared yet is retried instead of failing the whole
operation. The members come from Selenium's ``WebDriverException`` family,
narrowed down to its transient members in :mod:`app.yuque.wd_locator` — an
element that is missing, stale, or briefly covered — plus the standard
built-ins that describe a network or filesystem hiccup.

Keeping the tuple here — rather than in the automation module — is what lets
``base_page`` describe "not ready" without importing a driver library.
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
