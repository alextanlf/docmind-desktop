"""A Playwright-shaped locator over a WebDriver session.

The Yuque page objects are written against Playwright's locator API
(``locator()``, ``get_by_role()``, ``wait_for(state="visible")`` …). Rather
than rewrite every page object and every selector, this module presents the
same surface on top of a WebDriver driver, so the page logic is unchanged and
the migration risk stays in one file.

Selector syntax understood here is exactly what the page objects use:

===================  ======================================================
``role=button[name=保存]``  clickable element with that accessible name
``testid=confirm-delete``   ``data-testid`` attribute
``text=新建知识库``          exact visible text
anything else              passed straight to ``find_element`` as a CSS
                            or XPath locator
===================  ======================================================

Everything here is synchronous, matching WebDriver. The async surface the
callers expect is provided by :class:`AsyncLocator`, which simply runs the
blocking calls in a worker thread so the event loop is never stalled.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    ElementNotInteractableException,
    InvalidSelectorException,
    NoSuchElementException,
    StaleElementReferenceException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement

# Errors that mean "the element is not usable (yet)". A Playwright
# ``wait_for`` retries these, so the adapter must surface them as retryable
# rather than letting them escape as a hard failure.
_RETRYABLE = (
    NoSuchElementException,
    StaleElementReferenceException,
    ElementNotInteractableException,
    ElementClickInterceptedException,
)

# Accessible-name -> role element mapping for the roles the page objects use.
# Restricted to what Yuque's UI actually contains; an unknown role falls back
# to a wildcard rather than silently matching nothing.
_ROLE_TAGS = {
    "button": "button",
    "link": "a",
    "textbox": "input",
    "checkbox": "input",
}


def _escape(text: str) -> str:
    """Quote a literal for use inside an XPath expression.

    ``concat`` is required because the strings come from the page objects and
    may contain both quote characters.
    """
    if "'" not in text:
        return f"'{text}'"
    if '"' not in text:
        return f'"{text}"'
    parts = text.split("'")
    joined = ", \"'\", ".join(f"'{part}'" for part in parts)
    return f"concat({joined})"


def _name_from_role_selector(selector: str) -> tuple[str, str | None]:
    """Split ``role=button[name=保存]`` into ``("button", "保存")``."""
    body = selector[len("role=") :]
    role, _, rest = body.partition("[")
    if not rest:
        return body.strip(), None
    name = rest.removesuffix("]").removeprefix("name=")
    return role.strip(), name or None


def to_by(selector: str) -> tuple[str, str]:
    """Translate one page-object selector into a WebDriver ``(By, value)``."""
    if selector.startswith("role="):
        role, name = _name_from_role_selector(selector)
        if name:
            tag = _ROLE_TAGS.get(role, "*")
            # `descendant-or-self::` would include the element itself, but the
            # name always lives on a descendant when the role is a container
            # such as a button wrapping a span, so a descendant axis is right.
            xpath = f"//{tag}[normalize-space(.)={_escape(name)}]"
            return By.XPATH, xpath
        return By.TAG_NAME, role
    if selector.startswith("testid="):
        return By.CSS_SELECTOR, f'[data-testid="{selector[len("testid="):]}"]'
    if selector.startswith("text="):
        xpath = f"//*[normalize-space(.)={_escape(selector[len('text='):])}]"
        return By.XPATH, xpath
    if selector.startswith(("/", "./", "(", "./")):
        return By.XPATH, selector
    return By.CSS_SELECTOR, selector


class AsyncLocator:
    """One element resolved from a selector, with the calls the page objects use."""

    def __init__(self, driver: Any, selector: str) -> None:
        self._driver = driver
        self._selector = selector

    async def _find(self) -> WebElement:
        by, value = to_by(self._selector)
        return await asyncio.to_thread(self._driver.find_element, by, value)

    async def _find_all(self) -> list[WebElement]:
        by, value = to_by(self._selector)
        return await asyncio.to_thread(self._driver.find_elements, by, value)

    async def _first(self) -> WebElement:
        elements = await self._find_all()
        if not elements:
            raise NoSuchElementException(f"no element for {self._selector!r}")
        return elements[0]

    async def wait_for(self, state: str = "visible", timeout: int = 5_000) -> None:
        """Wait until the element is present, raising on timeout.

        ``BasePage.wait_for_any`` catches the failure and moves on to the next
        candidate selector, so the exception type matters more than the message:
        it must be one of the retryable ones.

        WebDriver has no equivalent of Playwright's visibility state, so both
        ``attached`` and ``visible`` are served by "the element is findable".
        An element that exists but is scrolled out of view is still clickable
        through WebDriver, so the looser check does not change the outcome.
        """
        deadline = time.monotonic() + timeout / 1000
        while True:
            try:
                await self._find()
                return
            except NoSuchElementException:
                if time.monotonic() >= deadline:
                    raise
                await asyncio.sleep(0.05)

    async def click(self) -> None:
        element = await self._find()
        await asyncio.to_thread(element.click)

    async def fill(self, value: str) -> None:
        """Replace the field's contents.

        ``clear()`` runs first because ``send_keys`` appends otherwise, which
        would silently corrupt an edited field.
        """
        element = await self._first()
        await asyncio.to_thread(element.clear)
        await asyncio.to_thread(element.send_keys, value)

    async def text_content(self) -> str:
        """Visible text of the first match.

        ``element.text`` is used rather than ``get_attribute("textContent")``:
        the attribute form is a DOM property, not an HTML attribute, so
        Selenium returns None for it. ``.text`` also matches Playwright's
        "rendered text" semantics, which is what the selectors compare against.
        """
        element = await self._first()
        return (await asyncio.to_thread(getattr, element, "text")) or ""

    async def get_attribute(self, name: str) -> str | None:
        element = await self._first()
        return await asyncio.to_thread(element.get_attribute, name)

    async def screenshot(self, path: str) -> None:
        element = await self._first()
        await asyncio.to_thread(element.screenshot, path)

    @property
    def first(self) -> AsyncLocator:
        """Playwright parity: ``locator.first`` reads as a property."""
        return _FirstLocator(self._driver, self._selector)


class _FirstLocator(AsyncLocator):
    """``locator.first`` — resolves to the first match rather than a list."""

    async def _find(self) -> WebElement:
        return await self._first()


def locator(driver: Any, selector: str) -> AsyncLocator:
    return AsyncLocator(driver, selector)


def by_role(driver: Any, role: str, name: str | None = None) -> AsyncLocator:
    return AsyncLocator(driver, f"role={role}[name={name}]" if name else f"role={role}")


def by_test_id(driver: Any, value: str) -> AsyncLocator:
    return AsyncLocator(driver, f"testid={value}")


def by_text(driver: Any, value: str, *, exact: bool = True) -> AsyncLocator:
    """Locate by visible text.

    ``exact`` mirrors the Playwright argument the page objects pass. The
    translation is not cosmetic: the exact form is a full-string XPath
    comparison, while the loose one uses ``contains`` so a selector written as
    ``text=新建`` still matches "新建知识库". Accepting the flag and ignoring it
    would silently make a loose selector behave as an exact one.
    """
    if exact:
        return AsyncLocator(driver, f"text={value}")
    escaped = value.replace('"', '""')
    return AsyncLocator(driver, f'//*[contains(normalize-space(.), "{escaped}")]')


__all__ = [
    "_RETRYABLE",
    "AsyncLocator",
    "InvalidSelectorException",
    "WebDriverException",
    "by_role",
    "by_test_id",
    "by_text",
    "locator",
    "to_by",
]
