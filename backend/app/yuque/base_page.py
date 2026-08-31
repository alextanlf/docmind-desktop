from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any, TypeVar

from playwright.async_api import Error as PlaywrightError

from app.api.errors import DomainError

T = TypeVar("T")

RETRY_DELAYS = (0.2, 0.5, 1.0)


class BasePage:
    """Small selector and retry boundary around a Playwright page."""

    def __init__(self, page: Any, screenshots_dir: Path | None = None, request_id: str = "yuque") -> None:
        self.page = page
        self.screenshots_dir = screenshots_dir
        self.request_id = request_id

    async def click_any(self, selectors: Sequence[str]) -> None:
        locator = await self.wait_for_any(selectors)
        await locator.click()

    async def fill_any(self, selectors: Sequence[str], value: str) -> None:
        locator = await self.wait_for_any(selectors)
        await locator.fill(value)

    async def wait_for_any(self, selectors: Sequence[str], timeout: int = 5_000) -> Any:
        last_error: Exception | None = None
        for selector in selectors:
            locator = self._locator(selector)
            try:
                await locator.wait_for(state="visible", timeout=timeout)
                return locator
            except _RETRYABLE_ERRORS as error:
                last_error = error
        raise DomainError("YUQUE_PAGE_CHANGED", "语雀页面结构已变化，请重新登录后重试", 503, True) from last_error

    async def with_retry(
        self, operation_name: str, operation: Callable[[], Awaitable[T]]
    ) -> T:
        for attempt, delay in enumerate((*RETRY_DELAYS, None)):
            try:
                return await operation()
            except DomainError:
                raise
            except _RETRYABLE_ERRORS:
                if delay is None:
                    await self._capture_failure(operation_name)
                    raise DomainError(
                        "YUQUE_PAGE_CHANGED", "语雀页面响应异常，请重新登录后重试", 503, True
                    ) from None
                await asyncio.sleep(delay)
        raise AssertionError("retry loop must return or raise")

    async def _capture_failure(self, operation_name: str) -> None:
        if self.screenshots_dir is None or not hasattr(self.page, "screenshot"):
            return
        self.screenshots_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        filename = f"{_safe_filename(self.request_id)}-{_safe_filename(operation_name)}.png"
        try:
            await self.page.screenshot(path=str(self.screenshots_dir / filename))
        except _RETRYABLE_ERRORS:
            return

    def _locator(self, selector: str) -> Any:
        if selector.startswith("role="):
            role, _, name = selector[5:].partition("[name=")
            return self.page.get_by_role(role, name=name.removesuffix("]") or None)
        if selector.startswith("testid="):
            return self.page.get_by_test_id(selector.removeprefix("testid="))
        if selector.startswith("text="):
            return self.page.get_by_text(selector.removeprefix("text="), exact=True)
        return self.page.locator(selector)


async def maybe_await(value: T | Awaitable[T]) -> T:
    if inspect.isawaitable(value):
        return await value
    return value


_RETRYABLE_ERRORS = (PlaywrightError, TimeoutError, ConnectionError, OSError)


def _safe_filename(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")
    return sanitized[:80] or "yuque"
