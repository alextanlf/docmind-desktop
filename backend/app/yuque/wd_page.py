"""A Playwright-shaped page facade over a WebDriver session.

``BasePage`` and the four Yuque page objects are written against a Playwright
page: ``goto`` with ``wait_until``/``timeout`` keywords, ``locator`` /
``get_by_role`` / ``get_by_test_id`` / ``get_by_text`` factories, a ``url``
property, and ``content`` / ``screenshot`` / ``add_style_tag``.

Rather than rewrite all of that against a synchronous, differently-shaped API,
:class:`WdPage` presents the same surface backed by a WebDriver session. The
page objects are then shared verbatim between the two gateways, so a selector
or a login heuristic can never drift between implementations.

The differences that genuinely cannot be papered over are handled explicitly:

* ``wait_until="commit"`` has no WebDriver equivalent. The original code uses
  it to start polling before a slow CDN script finishes; here the navigation
  is simply given a short page-load timeout and the caller's polling loop does
  the rest.
* ``wait_for_load_state`` returns whether the page reached a load state, and is
  used to tell "the page rendered but the DOM moved" apart from "the page never
  loaded" — that distinction drives two different error codes, so it is
  preserved rather than flattened.
"""
from __future__ import annotations

import asyncio
from typing import Any

from selenium.common.exceptions import WebDriverException

from app.yuque.wd_locator import (
    _RETRYABLE,
    AsyncLocator,
    by_role,
    by_test_id,
    by_text,
    locator,
)

# Selenium's own load-state strings, spelled the way ``wait_for_load_state``
# is called elsewhere. "interactive" is the closest to Playwright's
# "domcontentloaded": the DOM is parsed and scripts may still be running.
_LOAD_STATE_READY = "interactive"


class WdPage:
    """The subset of the Playwright page API the Yuque page objects rely on."""

    def __init__(self, session: Any, *, render_timeout_ms: int = 8_000) -> None:
        self._session = session
        self._render_timeout_ms = render_timeout_ms

    @property
    def session(self) -> Any:
        """The underlying WebDriver session.

        Exposed because the gateway has to persist cookies straight after a
        visible login, and the page facade is what it holds at that point.
        """
        return self._session

    # -- navigation --------------------------------------------------------

    async def goto(
        self,
        url: str,
        wait_until: str | None = None,
        timeout: int | None = None,
    ) -> None:
        """Navigate to ``url``.

        ``wait_until`` is accepted for signature compatibility. Playwright's
        "commit" has no WebDriver counterpart — it means "return as soon as the
        request is sent" — so the navigation is bounded by a short timeout and
        the caller's poll loop decides when the page is usable.
        """
        if wait_until == "commit" and timeout is not None:
            await self._session.goto(url, timeout_ms=timeout)
        else:
            await self._session.goto(
                url, timeout_ms=timeout or self._render_timeout_ms
            )

    @property
    def url(self) -> str:
        return self._session.driver.current_url

    async def title(self) -> str:
        return await self._session.title()

    async def content(self) -> str:
        return await self._session.source()

    # -- locators ----------------------------------------------------------

    def locator(self, selector: str) -> AsyncLocator:
        return locator(self._session.driver, selector)

    def get_by_role(self, role: str, name: str | None = None) -> AsyncLocator:
        return by_role(self._session.driver, role, name)

    def get_by_test_id(self, value: str) -> AsyncLocator:
        return by_test_id(self._session.driver, value)

    def get_by_text(self, value: str) -> AsyncLocator:
        return by_text(self._session.driver, value)

    # -- diagnostics -------------------------------------------------------

    async def wait_for_load_state(
        self, state: str = "domcontentloaded", timeout: int | None = None
    ) -> None:
        """Wait for a load state, raising on timeout like Playwright does.

        The caller catches failure to tell "rendered but DOM moved" apart from
        "never loaded", so this must signal failure rather than return False.
        """
        await asyncio.to_thread(
            self._wait_ready_async, timeout or self._render_timeout_ms
        )

    def _wait_ready_async(self, timeout_ms: int) -> None:
        # execute_async_script with a poll loop mirrors Playwright's
        # wait_for_load_state without blocking the event loop.
        script = (
            "var done = arguments[arguments.length - 1];"
            f"var deadline = Date.now() + {timeout_ms};"
            "function check(){"
            "  if (document.readyState === 'interactive' || document.readyState === 'complete')"
            "    return done(true);"
            "  if (Date.now() > deadline) return done(false);"
            "  setTimeout(check, 50);"
            "}"
            "check();"
        )
        self._session.driver.set_script_timeout(timeout_ms / 1000 + 5)
        if not self._session.driver.execute_async_script(script):
            raise WebDriverException("page did not reach a load state in time")

    async def screenshot(self, path: str) -> None:
        await self._session.screenshot(path)

    async def add_style_tag(self, content: str) -> None:
        """Inject a stylesheet.

        Used only to mask sensitive fields before a failure screenshot, so
        failure here is the caller's problem, not ours. The CSS travels as an
        argument rather than being interpolated into the script text, so a
        stray quote in the stylesheet cannot break the call.
        """
        await asyncio.to_thread(
            self._session.driver.execute_script,
            "const s = document.createElement('style');"
            "s.textContent = arguments[0];"
            "document.head.appendChild(s);",
            content,
        )

    # -- session passthrough ----------------------------------------------

    @property
    def driver(self) -> Any:
        return self._session.driver

    async def cookies(self) -> list[dict]:
        return await asyncio.to_thread(self._session.driver.get_cookies)


__all__ = ["_RETRYABLE", "WdPage"]
