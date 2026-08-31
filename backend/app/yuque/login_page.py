from __future__ import annotations

from app.api.errors import DomainError
from app.yuque.base_page import BasePage


class LoginPage(BasePage):
    is_logged_in_selector = "testid=dashboard"
    _logged_in_selectors = (
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
        try:
            await self.wait_for_any(self._logged_in_selectors, timeout=timeout)
        except DomainError:
            return False
        return True

    async def account_label(self) -> str | None:
        for selector in ("testid=account-label", "[data-testid=account-label]"):
            try:
                return (await (await self.wait_for_any((selector,), timeout=500)).inner_text()).strip()
            except DomainError:
                pass
        return None
