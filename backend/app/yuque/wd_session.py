"""Open a WebDriver session against the user's own Chrome.

The counterpart to Playwright's ``launch_persistent_context``. Unlike it,
every session starts clean, so a stored Yuque session is re-injected after
the first navigation — which WebDriver requires, since it refuses cookies for
a domain the browser is not currently on.

All WebDriver calls are blocking, so each one is dispatched to a worker
thread by :mod:`app.yuque.wd_locator`; this module keeps that in one place so
the page objects stay async.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from selenium.common.exceptions import WebDriverException

from app.api.errors import DomainError
from app.yuque import session as session_store
from app.yuque.browser import BrowserNotFoundError, find_browser
from app.yuque.driver import DriverUnavailableError, ensure_driver
from app.yuque.wd_locator import _RETRYABLE, locator

# Where the browser must land before cookies may be injected.
YUQUE_ORIGIN = "https://www.yuque.com"

# Chrome takes a few seconds to spawn; anything beyond this means the machine
# is under enough load that waiting longer only delays the user's answer.
_LAUNCH_TIMEOUT_SECONDS = 30.0
# Grace added on top of the page-load budget before the warm-up is abandoned.
_WARMUP_SLACK_SECONDS = 5.0

logger = logging.getLogger(__name__)

# Chrome flags shared by every session. ``--no-sandbox`` is required because
# the app ships a standalone runtime without the usual user namespaces, and
# automation is already opt-in per driver.
_BASE_ARGS = (
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    # Yuque renders behind a service worker; without this the automation
    # banner can swallow the first click on a freshly opened page.
    "--disable-features=Translate,MediaRouter",
)


class YuqueBrowserUnavailableError(DomainError):
    """No usable browser/driver pair on this machine."""


@dataclass
class BrowserSession:
    """A live WebDriver session plus the profile directory it belongs to."""

    driver: Any
    profile_dir: Path
    visible: bool

    def locator(self, selector: str):
        return locator(self.driver, selector)

    async def goto(self, url: str, timeout_ms: int = 30_000) -> None:
        """Navigate with a hard ceiling.

        ``set_page_load_timeout`` is best-effort and does not always interrupt a
        stalled navigation, so the call is wrapped in ``wait_for`` as well.
        Without that second guard a slow CDN can hold the call open well past
        the budget, which is exactly what the login probe must never do.
        """
        await asyncio.to_thread(self._apply_timeout, timeout_ms)
        await asyncio.wait_for(
            asyncio.to_thread(self.driver.get, url),
            timeout=timeout_ms / 1000 + _WARMUP_SLACK_SECONDS,
        )

    async def current_url(self) -> str:
        return await asyncio.to_thread(getattr, self.driver, "current_url")

    async def title(self) -> str:
        return await asyncio.to_thread(getattr, self.driver, "title")

    async def source(self) -> str:
        return await asyncio.to_thread(getattr, self.driver, "page_source")

    async def screenshot(self, path: str) -> None:
        await asyncio.to_thread(self.driver.save_screenshot, path)

    async def set_script(self, script: str) -> None:
        await asyncio.to_thread(self.driver.execute_script, script)

    async def quit(self) -> None:
        await asyncio.to_thread(self.driver.quit)

    def _apply_timeout(self, timeout_ms: int) -> None:
        setter = getattr(self.driver, "set_page_load_timeout", None)
        if setter is not None:
            setter(timeout_ms)


async def open_session(
    *,
    cache_root: Path,
    visible: bool,
    restore_session: bool,
    timeout_ms: int = 15_000,
) -> BrowserSession:
    """Start Chrome, optionally replaying the stored Yuque session.

    ``visible`` drives the windowed login flow; background operations use a
    headless window so nothing pops up mid-sync.
    """
    try:
        browser = find_browser()
    except BrowserNotFoundError as error:
        raise YuqueBrowserUnavailableError(
            "YUQUE_BROWSER_UNAVAILABLE",
            str(error),
            503,
            True,
            "安装 Chrome 后重试",
        ) from error

    try:
        bundle = await ensure_driver(browser, cache_root)
    except DriverUnavailableError as error:
        raise YuqueBrowserUnavailableError(
            "YUQUE_BROWSER_UNAVAILABLE", str(error), 503, True, "稍后重试"
        ) from error

    try:
        driver = await asyncio.wait_for(
            asyncio.to_thread(_launch, bundle.path, browser.executable, visible),
            timeout=_LAUNCH_TIMEOUT_SECONDS,
        )
    except TimeoutError as error:
        raise YuqueBrowserUnavailableError(
            "YUQUE_BROWSER_UNAVAILABLE", "启动浏览器超时，请重试", 503, True
        ) from error

    session = BrowserSession(
        driver=driver, profile_dir=cache_root, visible=visible
    )
    try:
        # Cookies can only be injected once the browser is on the domain, so
        # this first navigation is required — but it must not be able to hang
        # the caller. A stalled warm-up still yields a usable browser that can
        # report "not logged in", which matters because the desktop app probes
        # login state on its startup path.
        try:
            await asyncio.wait_for(
                session.goto(YUQUE_ORIGIN, timeout_ms=timeout_ms),
                timeout=timeout_ms / 1000 + _WARMUP_SLACK_SECONDS,
            )
            # Hide the automation flag before any script runs on the page.
            await session.set_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            )
            if restore_session:
                await _restore(session, cache_root)
        except (TimeoutError, WebDriverException, OSError):
            logger.warning("语雀会话预热失败，仍返回可用会话", exc_info=True)
    except DomainError:
        await session.quit()
        raise
    except Exception as error:
        await session.quit()
        raise YuqueBrowserUnavailableError(
            "YUQUE_BROWSER_UNAVAILABLE",
            "无法启动语雀登录浏览器，请重试",
            503,
            True,
        ) from error
    return session


async def _restore(session: BrowserSession, cache_root: Path) -> None:
    cookies = session_store.load(cache_root / "browser-data")
    if not cookies:
        return
    try:
        session_store.inject(session.driver, cookies)
    except DomainError:
        # The stored session is unusable; drop it so the next run starts from
        # a clean state instead of retrying the same stale cookies forever.
        session_store.clear(cache_root / "browser-data")
        raise


def persist_session(session: BrowserSession, cache_root: Path) -> None:
    """Save the live session after a successful login."""
    session_store.save(
        cache_root / "browser-data", session_store.capture(session.driver)
    )


def forget_session(cache_root: Path) -> None:
    session_store.clear(cache_root / "browser-data")


def has_stored_session(cache_root: Path) -> bool:
    return session_store.has_session(cache_root / "browser-data")


def _launch(driver_path: Path, browser_path: str, visible: bool) -> Any:
    """Build the driver. Blocking; always called through a worker thread."""
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service

    options = webdriver.ChromeOptions()
    options.binary_location = browser_path
    for argument in _BASE_ARGS:
        options.add_argument(argument)
    if not visible:
        # The branded browsers only support the "new" headless, which is a
        # real window rather than the old headless shell.
        options.add_argument("--headless=new")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    service = Service(str(driver_path))
    return webdriver.Chrome(service=service, options=options)


__all__ = [
    "YUQUE_ORIGIN",
    "_RETRYABLE",
    "BrowserSession",
    "YuqueBrowserUnavailableError",
    "forget_session",
    "has_stored_session",
    "open_session",
    "persist_session",
]
