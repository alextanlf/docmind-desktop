from __future__ import annotations

import asyncio

import pytest
from selenium.common.exceptions import (
    ElementNotInteractableException,
    NoSuchElementException,
    StaleElementReferenceException,
)
from selenium.webdriver.common.by import By

from app.yuque.wd_locator import (
    AsyncLocator,
    _escape,
    by_role,
    by_test_id,
    by_text,
    to_by,
)


class _FakeElement:
    def __init__(self, tag: str = "div", text: str = "") -> None:
        self.tag_name = tag
        self.text = text
        self.clicked = 0
        self.cleared = 0
        self.typed: list[str] = []
        self.attributes: dict[str, str] = {}
        self.interactable = True

    def click(self) -> None:
        if not self.interactable:
            raise ElementNotInteractableException("not interactable")
        self.clicked += 1

    def clear(self) -> None:
        self.cleared += 1

    def send_keys(self, value: str) -> None:
        self.typed.append(value)

    def get_attribute(self, name: str) -> str | None:
        return self.attributes.get(name)

    def screenshot(self, path: str) -> None:  # pragma: no cover - diagnostics only
        self.attributes["screenshot"] = path


class _FakeDriver:
    """Records lookups and hands back prepared elements."""

    def __init__(self, elements: list[_FakeElement] | None = None) -> None:
        self.elements = elements if elements is not None else []
        self.lookups: list[tuple[str, str]] = []
        self.raises: Exception | None = None

    def find_element(self, by: str, value: str) -> _FakeElement:
        self.lookups.append((by, value))
        if self.raises is not None:
            raise self.raises
        if not self.elements:
            raise NoSuchElementException("nothing")
        return self.elements[0]

    def find_elements(self, by: str, value: str) -> list[_FakeElement]:
        self.lookups.append((by, value))
        if self.raises is not None:
            raise self.raises
        return list(self.elements)


class TestSelectorTranslation:
    def test_role_with_name_becomes_a_scoped_xpath(self) -> None:
        by, value = to_by("role=button[name=保存]")

        assert by == By.XPATH
        assert value == "//button[normalize-space(.)='保存']"

    def test_role_without_name_uses_the_tag(self) -> None:
        assert to_by("role=button") == (By.TAG_NAME, "button")

    def test_roles_used_by_the_page_objects_map_to_tags(self) -> None:
        for role, tag in [("button", "button"), ("link", "a"), ("textbox", "input")]:
            _, value = to_by(f"role={role}[name=x]")
            assert f"//{tag}[" in value

    def test_unknown_role_falls_back_to_a_wildcard(self) -> None:
        """A role the table does not know must not silently match nothing."""
        _, value = to_by("role=menuitem[name=文件]")

        assert value.startswith("//*[normalize-space(.)='文件']")

    def test_testid_becomes_a_data_attribute_selector(self) -> None:
        assert to_by("testid=confirm-delete") == (
            By.CSS_SELECTOR,
            '[data-testid="confirm-delete"]',
        )

    def test_text_becomes_an_exact_xpath(self) -> None:
        _, value = to_by("text=新建知识库")

        assert value == "//*[normalize-space(.)='新建知识库']"

    def test_plain_css_is_passed_through(self) -> None:
        assert to_by("a.book-link") == (By.CSS_SELECTOR, "a.book-link")

    def test_xpath_is_recognised_and_passed_through(self) -> None:
        assert to_by("//div[@id='x']") == (By.XPATH, "//div[@id='x']")

    @pytest.mark.parametrize(
        ("name", "marker"),
        [
            # A single quote switches the literal to double quotes, which is
            # simpler than concat and just as valid in XPath.
            ("it's here", '"it\'s here"'),
            # Both kinds present: only concat can express that.
            ('mix \' and "', "concat("),
        ],
    )
    def test_quotes_in_a_name_are_escaped(self, name: str, marker: str) -> None:
        """Yuque's own copy contains quotes; a naive template would break."""
        _, value = to_by(f"role=button[name={name}]")

        assert marker in value

    def test_escape_prefers_single_quotes(self) -> None:
        assert _escape("plain") == "'plain'"


class TestLocatorOperations:
    async def test_click_dispatches_to_the_element(self) -> None:
        element = _FakeElement(tag="button")
        locator = AsyncLocator(_FakeDriver([element]), "role=button[name=保存]")

        await locator.click()

        assert element.clicked == 1

    async def test_fill_replaces_rather_than_appends(self) -> None:
        """``send_keys`` alone appends, which corrupts an edited field."""
        element = _FakeElement(tag="input")
        locator = AsyncLocator(_FakeDriver([element]), "//input")

        await locator.fill("hello")

        assert element.cleared == 1
        assert element.typed == ["hello"]

    async def test_first_resolves_to_the_head_of_the_list(self) -> None:
        head, tail = _FakeElement(text="one"), _FakeElement(text="two")
        locator = AsyncLocator(_FakeDriver([head, tail]), "text=x")

        assert (await locator.first.text_content()) == "one"

    async def test_missing_element_raises_rather_than_returning_none(self) -> None:
        locator = AsyncLocator(_FakeDriver([]), "text=missing")

        with pytest.raises(NoSuchElementException):
            await locator.first.text_content()

    async def test_text_content_uses_rendered_text(self) -> None:
        """``get_attribute("textContent")`` returns None in Selenium."""
        element = _FakeElement(text="可见文本")
        locator = AsyncLocator(_FakeDriver([element]), "text=x")

        assert await locator.text_content() == "可见文本"

    async def test_a_stale_element_propagates_for_the_retry_loop(self) -> None:
        """The retry wrapper keys off these; swallowing them would hide failures."""
        driver = _FakeDriver()
        driver.raises = StaleElementReferenceException("stale")
        locator = AsyncLocator(driver, "text=x")

        with pytest.raises(StaleElementReferenceException):
            await locator.click()

    async def test_helpers_produce_the_expected_selectors(self) -> None:
        driver = _FakeDriver([_FakeElement()])

        await by_role(driver, "button", "保存").click()
        await by_test_id(driver, "create-document").click()
        await by_text(driver, "新建知识库").click()

        values = [value for _by, value in driver.lookups]
        assert "//button[normalize-space(.)='保存']" in values
        assert '[data-testid="create-document"]' in values
        assert "//*[normalize-space(.)='新建知识库']" in values


class TestBrowserCallsStayOffTheEventLoop:
    async def test_blocking_calls_run_in_a_worker(self, monkeypatch) -> None:
        """A synchronous ``driver.get`` on the event loop stalls the app.

        Selenium is entirely blocking, so every call has to hop a thread;
        this checks the wrapper exists rather than trusting the pattern.
        """
        import app.yuque.wd_locator as module

        seen: list[str] = []
        original = asyncio.to_thread

        async def _spy(func, /, *args, **kwargs):
            seen.append(getattr(func, "__name__", "call"))
            return await original(func, *args, **kwargs)

        monkeypatch.setattr(module.asyncio, "to_thread", _spy)
        driver = _FakeDriver([_FakeElement()])

        await AsyncLocator(driver, "text=x").click()

        # Both the lookup and the element's own click must leave the loop;
        # a single blocking call would freeze the whole app.
        assert seen == ["find_element", "click"]
