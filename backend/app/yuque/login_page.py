from __future__ import annotations

import time
from urllib.parse import urlparse

from app.api.errors import DomainError
from app.yuque.base_page import BasePage


class LoginPage(BasePage):
    is_logged_in_selector = "testid=dashboard"
    _logged_in_selectors = (
        "testid=dashboard:index",
        "testid=dashboard-index",
        "role=link[name=工作台]",
        "testid=dashboard",
        "text=工作台",
    )

    async def is_logged_in(self) -> bool:
        try:
            await self.wait_for_any(self._logged_in_selectors, timeout=1_000)
        except DomainError:
            return False
        return True

    async def wait_until_logged_in(self, timeout: int = 600_000) -> bool:
        deadline = time.monotonic() + timeout / 1_000
        while True:
            if _is_dashboard_url(self.page.url):
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            for selector in self._logged_in_selectors:
                if _is_dashboard_url(self.page.url):
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                try:
                    await self.wait_for_any((selector,), timeout=max(1, min(1_000, int(remaining * 1_000))))
                    return True
                except DomainError:
                    continue

    async def account_label(self) -> str | None:
        for selector in ("testid=account-label", "[data-testid=account-label]"):
            try:
                return (await (await self.wait_for_any((selector,), timeout=500)).inner_text()).strip()
            except DomainError:
                pass
        return None


def _is_dashboard_url(url: str) -> bool:
    return urlparse(url).path.rstrip("/") == "/dashboard"
