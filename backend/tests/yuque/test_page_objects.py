from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from app.api.errors import DomainError
from app.config import AppSettings
from app.schemas.remote import (
    CreateRemoteDocumentRequest,
    CreateRemoteRepositoryRequest,
    RemoteDocument,
    RemoteDocumentContent,
    UpdateRemoteDocumentRequest,
)
from app.yuque import browser as browser_module
from app.yuque.base_page import BasePage
from app.yuque.dashboard_page import DashboardPage
from app.yuque.editor_page import EditorPage
from app.yuque.login_page import LoginPage
from app.yuque.session import StoredCookie
from app.yuque.session import save as save_session
from app.yuque.wd_gateway import WebDriverYuqueGateway
from app.yuque.wd_session import YuqueBrowserUnavailableError


class FixtureLocator:
    def __init__(self, page: FixturePage, selector: str, visible: bool) -> None:
        self.page = page
        self.selector = selector
        self.visible = visible

    async def wait_for(self, state: str = "visible", timeout: int | None = None) -> None:
        del state
        self.page.waited_selectors.append(self.selector)
        self.page.wait_timeouts.append(timeout)
        if self.page.wait_hook is not None:
            self.page.wait_hook(timeout or 0)
        if not self.visible:
            raise TimeoutError(self.selector)

    async def click(self) -> None:
        if not self.visible:
            raise TimeoutError(self.selector)
        self.page.clicked.append(self.selector)
        if self.selector == "[data-testid=create-repository-submit]" and self.page.repository_submit_error:
            raise self.page.repository_submit_error
        if self.selector == "[data-testid=editor-save]" and self.page.document_save_error:
            raise self.page.document_save_error
        if self.selector == "[data-testid=editor-save]" and self.page.document_url_after_save:
            self.page.url = self.page.document_url_after_save
        if (
            self.selector == "[data-testid=editor-save]"
            and self.page.document_title_after_save is not None
        ):
            self.page.values["[data-testid=editor-title]"] = self.page.document_title_after_save

    async def fill(self, value: str) -> None:
        if not self.visible:
            raise TimeoutError(self.selector)
        self.page.fill_attempts.append((self.selector, value))
        self.page.filled[self.selector] = value
        self.page.values[self.selector] = value

    async def all(self) -> list[FixtureLocator]:
        if self.selector == "[data-testid=document-link]" and self.page.document_list_visibility_after is not None:
            self.page.document_list_calls += 1
            if self.page.document_list_calls <= self.page.document_list_visibility_after:
                return []
        if self.selector == "[data-testid=repository-link]" and self.page.repository_list_visibility_after is not None:
            self.page.repository_list_calls += 1
            if self.page.repository_list_calls <= self.page.repository_list_visibility_after:
                return []
        return [self] if self.visible else []

    async def inner_text(self) -> str:
        return self.page.text.get(self.selector, "")

    async def get_attribute(self, name: str) -> str | None:
        return self.page.attributes.get((self.selector, name))

    async def input_value(self) -> str:
        return self.page.values.get(self.selector, "")


class FixtureCookieContext:
    """Minimal stand-in for a BrowserContext that exposes stored cookies."""

    def __init__(self, cookies: list[dict[str, Any]]) -> None:
        self._cookies = cookies

    async def cookies(self, urls: str | None = None) -> list[dict[str, Any]]:
        del urls
        return list(self._cookies)


class FixtureDriver:
    """Just enough driver for ``session_store.capture`` to read cookies."""

    def __init__(self, cookies: list[dict[str, Any]]) -> None:
        self._cookies = cookies

    def get_cookies(self) -> list[dict[str, Any]]:
        return list(self._cookies)


class FixtureSession:
    """Stand-in for ``BrowserSession``, as exposed by ``WdPage.session``."""

    def __init__(self, cookies: list[dict[str, Any]]) -> None:
        self.driver = FixtureDriver(cookies)


class FixturePage:
    def __init__(
        self,
        available: set[str],
        fixture: Path | None = None,
        cookies: list[dict[str, Any]] | None = None,
    ) -> None:
        self.available = available
        self.fixture = fixture
        self.context: FixtureCookieContext | None = None
        # The WebDriver gateway persists the live session after a successful
        # login (``persist_session(page.session, ...)``), so the page has to
        # expose one carrying whatever cookies the fixture declares.
        self.session = FixtureSession(cookies or [])
        self.clicked: list[str] = []
        self.fill_attempts: list[tuple[str, str]] = []
        self.filled: dict[str, str] = {}
        self.text: dict[str, str] = {}
        self.attributes: dict[tuple[str, str], str] = {}
        self.values: dict[str, str] = {}
        self.screenshots: list[Path] = []
        self.gotos: list[str] = []
        self.url = "about:blank"
        self.redirect_url: str | None = None
        self.wait_timeouts: list[int | None] = []
        self.waited_selectors: list[str] = []
        self.wait_hook = None
        self.mask_styles: list[str] = []
        self.resource_goto_failures = 0
        self.login_goto_failures = 0
        self.load_state_hook: Any = None
        self.goto_attempts: list[str] = []
        self.document_list_visibility_after: int | None = None
        self.document_list_calls = 0
        self.repository_list_visibility_after: int | None = None
        self.repository_list_calls = 0
        self.repository_submit_error: Exception | None = None
        self.document_save_error: Exception | None = None
        self.document_url_after_save: str | None = None
        self.document_title_after_save: str | None = None
        self.dom_html = "<html><body>fixture</body></html>"

    def locator(self, selector: str) -> FixtureLocator:
        return FixtureLocator(self, selector, selector in self.available)

    def get_by_role(self, role: str, name: str | None = None) -> FixtureLocator:
        return self.locator(f"role={role}[name={name}]")

    def get_by_test_id(self, test_id: str) -> FixtureLocator:
        return self.locator(f"[data-testid={test_id}]")

    def get_by_text(self, text: str, exact: bool = True) -> FixtureLocator:
        return self.locator(f"text={text}[exact={exact}]")

    async def screenshot(self, path: str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"fixture-png")
        self.screenshots.append(target)

    async def add_style_tag(self, content: str) -> None:
        self.mask_styles.append(content)

    async def content(self) -> str:
        return self.dom_html

    async def wait_for_load_state(self, state: str = "load", timeout: int | None = None) -> None:
        del state, timeout
        if self.load_state_hook is not None:
            await self.load_state_hook()

    async def goto(
        self, url: str, wait_until: str = "load", timeout: int | None = None
    ) -> None:
        del wait_until, timeout
        self.goto_attempts.append(url)
        if url == "https://www.yuque.com/login" and self.login_goto_failures:
            self.login_goto_failures -= 1
            raise TimeoutError("login navigation timeout")
        if url != "https://www.yuque.com/dashboard" and self.resource_goto_failures:
            self.resource_goto_failures -= 1
            raise TimeoutError("navigation timeout")
        self.url = url
        if self.redirect_url is not None:
            self.url = self.redirect_url
        self.gotos.append(url)


def test_login_page_detects_logged_out_local_fixture() -> None:
    fixture = Path(__file__).parent / "fixtures" / "yuque-dashboard.html"
    page = FixturePage(set(), fixture)

    assert fixture.read_text(encoding="utf-8")
    assert LoginPage(page).is_logged_in_selector is not None


async def test_login_page_reads_visible_account_label() -> None:
    page = FixturePage({"[data-testid=account-label]"})
    page.text["[data-testid=account-label]"] = "alice@example.test"

    assert await LoginPage(page).account_label() == "alice@example.test"


async def test_real_gateway_checks_login_after_navigating_to_local_dashboard(
    tmp_path: Path,
) -> None:
    settings = AppSettings(session_token=SecretStr("token"), data_dir=tmp_path)
    gateway = WebDriverYuqueGateway(settings)
    page = FixturePage({"[data-testid=dashboard]"})

    @asynccontextmanager
    async def fake_new_page(*, visible_login: bool):
        assert visible_login is False
        yield page

    gateway._new_page = fake_new_page  # type: ignore[method-assign]

    status = await gateway.login_status()

    assert page.gotos == ["https://www.yuque.com/dashboard"]
    assert status.logged_in is True


async def test_real_gateway_login_status_stays_bounded_when_page_never_renders(
    tmp_path: Path,
) -> None:
    """A stalled page must not hang the desktop startup check."""
    settings = AppSettings(session_token=SecretStr("token"), data_dir=tmp_path)
    gateway = WebDriverYuqueGateway(settings)
    page = FixturePage(set())

    async def never_renders() -> None:
        raise TimeoutError("page did not reach a load state in time")

    page.load_state_hook = never_renders

    @asynccontextmanager
    async def fake_new_page(*, visible_login: bool):
        assert visible_login is False
        yield page

    gateway._new_page = fake_new_page  # type: ignore[method-assign]

    started = time.monotonic()
    status = await gateway.login_status()
    elapsed = time.monotonic() - started

    assert status.logged_in is False
    assert status.requires_login is True
    assert elapsed < 8
    assert page.goto_attempts == ["https://www.yuque.com/dashboard"]


async def test_real_gateway_login_status_falls_back_to_session_cookie(tmp_path: Path) -> None:
    """Yuque's shell cannot render without its CDN; the stored cookie still counts.

    The two stacks read the session from different places: Playwright could ask
    its persistent profile for cookies in memory, whereas WebDriver keeps no
    profile at all, so the login state lives in a file this gateway owns. The
    fallback therefore has to be arranged on disk here.
    """
    settings = AppSettings(session_token=SecretStr("token"), data_dir=tmp_path)
    gateway = WebDriverYuqueGateway(settings)
    page = FixturePage(set())
    save_session(
        tmp_path / "browser-data",
        [StoredCookie("_yuque_session", "secret", ".yuque.com", "/")],
    )

    async def never_renders() -> None:
        raise TimeoutError("page did not reach a load state in time")

    page.load_state_hook = never_renders

    @asynccontextmanager
    async def fake_new_page(*, visible_login: bool):
        assert visible_login is False
        yield page

    gateway._new_page = fake_new_page  # type: ignore[method-assign]

    status = await gateway.login_status()

    assert status.logged_in is True
    assert status.requires_login is False


async def test_real_gateway_reports_logged_out_status_without_raising(tmp_path: Path) -> None:
    settings = AppSettings(session_token=SecretStr("token"), data_dir=tmp_path)
    gateway = WebDriverYuqueGateway(settings)
    page = FixturePage(set())

    @asynccontextmanager
    async def fake_new_page(*, visible_login: bool):
        assert visible_login is False
        yield page

    gateway._new_page = fake_new_page  # type: ignore[method-assign]
    page.redirect_url = "https://www.yuque.com/login"

    status = await gateway.login_status()

    assert status.logged_in is False
    assert status.requires_login is True


async def test_real_gateway_reports_logged_out_when_browser_unavailable(tmp_path: Path) -> None:
    settings = AppSettings(session_token=SecretStr("token"), data_dir=tmp_path)
    gateway = WebDriverYuqueGateway(settings)

    @asynccontextmanager
    async def unavailable_new_page(*, visible_login: bool):
        assert visible_login is False
        raise YuqueBrowserUnavailableError(
            "YUQUE_BROWSER_UNAVAILABLE",
            "本机未找到可用的 Google Chrome",
            503,
            True,
        )
        yield

    gateway._new_page = unavailable_new_page  # type: ignore[method-assign]

    status = await gateway.login_status()

    assert status.logged_in is False
    assert status.account_label is None
    assert status.requires_login is True


async def test_begin_login_reports_missing_browser_without_generic_login_error(
    tmp_path: Path,
) -> None:
    gateway = WebDriverYuqueGateway(AppSettings(session_token=SecretStr("token"), data_dir=tmp_path))

    @asynccontextmanager
    async def unavailable_new_page(*, visible_login: bool):
        assert visible_login is True
        raise YuqueBrowserUnavailableError(
            "YUQUE_BROWSER_UNAVAILABLE",
            "本机未找到可用的 Google Chrome",
            503,
            True,
        )
        yield

    gateway._new_page = unavailable_new_page  # type: ignore[method-assign]

    with pytest.raises(DomainError) as error:
        await gateway.begin_login()

    assert error.value.code == "YUQUE_BROWSER_UNAVAILABLE"
    assert error.value.retryable is True


async def test_real_gateway_maps_expired_cookie_to_login_required(tmp_path: Path) -> None:
    settings = AppSettings(session_token=SecretStr("token"), data_dir=tmp_path)
    gateway = WebDriverYuqueGateway(settings)
    page = FixturePage(set())

    @asynccontextmanager
    async def fake_new_page(*, visible_login: bool):
        assert visible_login is False
        yield page

    gateway._new_page = fake_new_page  # type: ignore[method-assign]
    page.redirect_url = "https://www.yuque.com/login"

    with pytest.raises(DomainError) as error:
        await gateway.list_repositories()

    assert error.value.code == "YUQUE_LOGIN_REQUIRED"


async def test_real_gateway_opens_requested_repository_before_listing_documents(tmp_path: Path) -> None:
    settings = AppSettings(session_token=SecretStr("token"), data_dir=tmp_path)
    gateway = WebDriverYuqueGateway(settings)
    page = FixturePage({"[data-testid=dashboard]", "[data-testid=document-link]"})
    page.text["[data-testid=document-link]"] = "State"
    page.attributes[("[data-testid=document-link]", "href")] = "/swiftui/state"

    @asynccontextmanager
    async def fake_new_page(*, visible_login: bool):
        assert visible_login is False
        yield page

    gateway._new_page = fake_new_page  # type: ignore[method-assign]

    documents = await gateway.list_documents("swiftui")

    assert documents[0].title == "State"
    assert page.gotos == ["https://www.yuque.com/dashboard", "https://www.yuque.com/swiftui"]


async def test_dashboard_extracts_repository_from_test_id_fallback() -> None:
    page = FixturePage({"[data-testid=repository-link]"})
    page.text["[data-testid=repository-link]"] = "SwiftUI"
    page.attributes[("[data-testid=repository-link]", "href")] = "/swiftui"

    repositories = await DashboardPage(page).list_repositories()

    assert [(repository.name, repository.url) for repository in repositories] == [
        ("SwiftUI", "/swiftui")
    ]


async def test_editor_imports_markdown_and_waits_for_save_confirmation() -> None:
    page = FixturePage(
        {
            "[data-testid=editor-markdown]",
            "[data-testid=editor-save]",
            "[data-testid=save-confirmation]",
        }
    )

    await EditorPage(page).import_markdown("# State")

    assert page.filled["[data-testid=editor-markdown]"] == "# State"
    assert page.clicked == ["[data-testid=editor-save]"]


async def test_base_page_retries_three_times_with_required_delays_then_writes_screenshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = FixturePage(set())
    base = BasePage(page, screenshots_dir=tmp_path, request_id="request token / unsafe")
    attempts = 0
    delays: list[float] = []

    async def record_delay(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("app.yuque.base_page.asyncio.sleep", record_delay)

    async def fail() -> None:
        nonlocal attempts
        attempts += 1
        raise TimeoutError("not available")

    with pytest.raises(DomainError) as error:
        await base.with_retry("save document", fail)

    assert error.value.code == "YUQUE_PAGE_CHANGED"
    assert attempts == 4
    assert delays == [0.2, 0.5, 1.0]
    assert [path.name for path in page.screenshots] == ["request-token-unsafe-save-document.png"]
    assert page.screenshots[0].read_bytes() == b"fixture-png"


async def test_base_page_retries_retryable_selector_domain_error_before_screenshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = FixturePage(set())
    base = BasePage(page, screenshots_dir=tmp_path, request_id="request")
    attempts = 0

    async def no_delay(_: float) -> None:
        return None

    monkeypatch.setattr("app.yuque.base_page.asyncio.sleep", no_delay)

    async def click_missing() -> None:
        nonlocal attempts
        attempts += 1
        await base.click_any(("testid=missing",))

    with pytest.raises(DomainError) as error:
        await base.with_retry("selector", click_missing)

    assert error.value.code == "YUQUE_PAGE_CHANGED"
    assert attempts == 4
    assert len(page.screenshots) == 1


async def test_gateway_retries_resource_navigation_inside_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = AppSettings(session_token=SecretStr("token"), data_dir=tmp_path)
    gateway = WebDriverYuqueGateway(settings)
    page = FixturePage({"[data-testid=dashboard]", "[data-testid=document-link]"})
    page.text["[data-testid=document-link]"] = "State"
    page.resource_goto_failures = 1

    async def no_delay(_: float) -> None:
        return None

    monkeypatch.setattr("app.yuque.base_page.asyncio.sleep", no_delay)

    @asynccontextmanager
    async def fake_new_page(*, visible_login: bool):
        assert visible_login is False
        yield page

    gateway._new_page = fake_new_page  # type: ignore[method-assign]

    assert (await gateway.list_documents("swiftui"))[0].title == "State"
    assert page.gotos.count("https://www.yuque.com/swiftui") == 1


async def test_login_page_accepts_dashboard_url_without_selector() -> None:
    page = FixturePage(set())
    page.url = "https://www.yuque.com/dashboard"

    assert await LoginPage(page).wait_until_logged_in(timeout=1_000) is True


async def test_login_page_never_exceeds_total_login_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    page = FixturePage(set())
    clock = [0.0]
    page.wait_hook = lambda timeout: clock.__setitem__(0, clock[0] + timeout / 1000)
    monkeypatch.setattr("app.yuque.login_page.time.monotonic", lambda: clock[0])

    assert await LoginPage(page).wait_until_logged_in(timeout=3_000) is False
    assert sum(timeout or 0 for timeout in page.wait_timeouts) <= 3_000


async def test_failure_screenshot_masks_sensitive_page_material(tmp_path: Path) -> None:
    page = FixturePage(set())
    base = BasePage(page, screenshots_dir=tmp_path, request_id="request")

    async def fail() -> None:
        raise TimeoutError("not available")

    with pytest.raises(DomainError):
        await base.with_retry("login-timeout", fail)

    assert page.mask_styles
    assert "input" in page.mask_styles[0]
    assert "qrcode" in page.mask_styles[0]
    assert page.screenshots


async def test_new_page_serializes_sessions_and_quits_each_one_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One browser session at a time, and none is left running.

    The Playwright gateway reused a long-lived manager across calls; the
    WebDriver gateway starts a fresh session per operation instead, so the
    invariant worth locking down is unchanged in spirit but different in
    mechanism: concurrent callers must not hold a session at the same time,
    every session must be quit exactly once, and a visible login must not
    inherit the stored cookie.
    """

    class FakeSession:
        def __init__(self) -> None:
            self.quit_calls = 0

        async def quit(self) -> None:
            self.quit_calls += 1
            # Yield so an unserialised implementation would interleave here.
            await asyncio.sleep(0)

    class FakeSessionFactory:
        def __init__(self) -> None:
            self.active_sessions = 0
            self.max_active_sessions = 0
            self.sessions: list[FakeSession] = []
            self.calls: list[dict[str, Any]] = []

        async def open(
            self, *, cache_root: Path, visible: bool, restore_session: bool
        ) -> FakeSession:
            self.calls.append(
                {"cache_root": cache_root, "visible": visible, "restore_session": restore_session}
            )
            self.active_sessions += 1
            self.max_active_sessions = max(
                self.max_active_sessions, self.active_sessions
            )
            session = FakeSession()
            self.sessions.append(session)
            original_quit = session.quit

            async def quit() -> None:
                self.active_sessions -= 1
                await original_quit()

            session.quit = quit  # type: ignore[method-assign]
            return session

    factory = FakeSessionFactory()

    async def fake_open_session(
        *, cache_root: Path, visible: bool, restore_session: bool
    ) -> FakeSession:
        return await factory.open(
            cache_root=cache_root, visible=visible, restore_session=restore_session
        )

    monkeypatch.setattr("app.yuque.wd_gateway.open_session", fake_open_session)
    gateway = WebDriverYuqueGateway(
        AppSettings(session_token=SecretStr("token"), data_dir=tmp_path)
    )

    async def use_page(visible_login: bool) -> None:
        async with gateway._new_page(visible_login=visible_login):
            await asyncio.sleep(0)

    await asyncio.gather(use_page(False), use_page(True))

    # Serialisation: the two callers never overlapped.
    assert factory.max_active_sessions == 1
    # Every session is torn down exactly once, none leaked.
    assert [session.quit_calls for session in factory.sessions] == [1, 1]
    assert factory.active_sessions == 0
    # A background page restores the stored cookie; a visible login must
    # start clean so it cannot be short-circuited by a stale session.
    assert sorted(call["visible"] for call in factory.calls) == [False, True]
    assert {call["visible"]: call["restore_session"] for call in factory.calls} == {
        False: True,
        True: False,
    }


async def test_begin_login_waits_for_regular_context_work_instead_of_reporting_conflict(
    tmp_path: Path,
) -> None:
    gateway = WebDriverYuqueGateway(AppSettings(session_token=SecretStr("token"), data_dir=tmp_path))
    page = FixturePage({"[data-testid=dashboard]"})

    @asynccontextmanager
    async def fake_new_page(*, visible_login: bool):
        assert visible_login is True
        async with gateway._context_lock:
            yield page

    gateway._new_page = fake_new_page  # type: ignore[method-assign]
    await gateway._context_lock.acquire()
    pending = asyncio.create_task(gateway.begin_login())
    await asyncio.sleep(0)

    assert not pending.done()
    gateway._context_lock.release()
    assert (await pending).logged_in is True


async def test_real_gateway_read_and_update_return_document_metadata(tmp_path: Path) -> None:
    gateway = WebDriverYuqueGateway(AppSettings(session_token=SecretStr("token"), data_dir=tmp_path))
    page = FixturePage(
        {
            "[data-testid=dashboard]",
            "[data-testid=editor-markdown]",
            "[data-testid=editor-title]",
            "[data-testid=editor-save]",
            "[data-testid=save-confirmation]",
        }
    )
    page.values["[data-testid=editor-markdown]"] = "# State"
    page.values["[data-testid=editor-title]"] = "State"

    @asynccontextmanager
    async def fake_new_page(*, visible_login: bool):
        assert visible_login is False
        yield page

    gateway._new_page = fake_new_page  # type: ignore[method-assign]

    read = await gateway.read_document("swiftui/state")
    updated = await gateway.update_document(
        UpdateRemoteDocumentRequest(document_id="swiftui/state", title="State 2", content="# State 2")
    )

    assert (read.repository_id, read.title) == ("swiftui", "State")
    assert (updated.repository_id, updated.title) == ("swiftui", "State 2")


async def test_create_missing_document_and_delete_confirmation_are_retryable(tmp_path: Path) -> None:
    gateway = WebDriverYuqueGateway(AppSettings(session_token=SecretStr("token"), data_dir=tmp_path))
    page = FixturePage(
        {
            "[data-testid=dashboard]",
            "[data-testid=create-document]",
            "[data-testid=editor-title]",
            "[data-testid=editor-markdown]",
            "[data-testid=editor-save]",
            "[data-testid=save-confirmation]",
            "[data-testid=document-link]",
            "[data-testid=delete-document]",
            "[data-testid=confirm-delete]",
            "[data-testid=delete-confirmation]",
        }
    )
    page.text["[data-testid=document-link]"] = "Other"

    @asynccontextmanager
    async def fake_new_page(*, visible_login: bool):
        assert visible_login is False
        yield page

    gateway._new_page = fake_new_page  # type: ignore[method-assign]

    with pytest.raises(DomainError) as error:
        await gateway.create_document(
            CreateRemoteDocumentRequest(repository_id="swiftui", title="State", content="# State")
        )
    await gateway.delete_document("swiftui/state", "swiftui")

    assert error.value.code == "YUQUE_PAGE_CHANGED"
    assert "[data-testid=delete-confirmation]" in page.waited_selectors
    assert page.clicked[-2:] == ["[data-testid=delete-document]", "[data-testid=confirm-delete]"]


async def test_dashboard_without_stable_selector_maps_to_page_changed(tmp_path: Path) -> None:
    gateway = WebDriverYuqueGateway(AppSettings(session_token=SecretStr("token"), data_dir=tmp_path))
    page = FixturePage(set())

    @asynccontextmanager
    async def fake_new_page(*, visible_login: bool):
        assert visible_login is False
        yield page

    gateway._new_page = fake_new_page  # type: ignore[method-assign]

    with pytest.raises(DomainError) as error:
        await gateway.login_status()

    assert error.value.code == "YUQUE_PAGE_CHANGED"


async def test_visible_login_navigation_retries_before_capturing_masked_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = WebDriverYuqueGateway(AppSettings(session_token=SecretStr("token"), data_dir=tmp_path))
    page = FixturePage(set())
    page.login_goto_failures = 4

    async def no_delay(_: float) -> None:
        return None

    monkeypatch.setattr("app.yuque.base_page.asyncio.sleep", no_delay)

    @asynccontextmanager
    async def fake_new_page(*, visible_login: bool):
        assert visible_login is True
        yield page

    gateway._new_page = fake_new_page  # type: ignore[method-assign]

    with pytest.raises(DomainError) as error:
        await gateway.begin_login()

    assert error.value.code == "YUQUE_PAGE_CHANGED"
    assert page.goto_attempts == ["https://www.yuque.com/login"] * 4
    assert page.mask_styles
    assert len(page.screenshots) == 1


async def test_repository_creation_retries_visibility_without_submitting_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = FixturePage(
        {
            "[data-testid=dashboard]",
            "[data-testid=create-repository]",
            "[data-testid=repository-name]",
            "[data-testid=create-repository-submit]",
            "[data-testid=repository-link]",
        }
    )
    page.text["[data-testid=repository-link]"] = "SwiftUI"
    page.repository_list_visibility_after = 1
    gateway = WebDriverYuqueGateway(AppSettings(session_token=SecretStr("token"), data_dir=tmp_path))

    async def no_delay(_: float) -> None:
        return None

    monkeypatch.setattr("app.yuque.base_page.asyncio.sleep", no_delay)

    @asynccontextmanager
    async def fake_new_page(*, visible_login: bool):
        assert visible_login is False
        yield page

    gateway._new_page = fake_new_page  # type: ignore[method-assign]

    created = await gateway.create_repository(CreateRemoteRepositoryRequest(name="SwiftUI"))

    assert created.name == "SwiftUI"
    assert page.repository_list_calls == 2
    assert page.clicked.count("[data-testid=create-repository]") == 1
    assert page.fill_attempts.count(("[data-testid=repository-name]", "SwiftUI")) == 1
    assert page.clicked.count("[data-testid=create-repository-submit]") == 1


@pytest.mark.parametrize(
    ("submit_error", "private_values"),
    [
        (
            TimeoutError("repository create response timed out"),
            ("repository create response timed out",),
        ),
        (
            DomainError(
                "YUQUE_INTERNAL_SUBMIT_FAILURE",
                "repository create failed for secret=session-cookie-123",
                418,
                True,
                "retry with session-cookie-123",
            ),
            (
                "YUQUE_INTERNAL_SUBMIT_FAILURE",
                "repository create failed for secret=session-cookie-123",
                "session-cookie-123",
            ),
        ),
    ],
    ids=["timeout", "retryable-domain-error"],
)
async def test_repository_creation_submit_error_is_not_replayed(
    tmp_path: Path, submit_error: Exception, private_values: tuple[str, ...]
) -> None:
    page = FixturePage(
        {
            "[data-testid=dashboard]",
            "[data-testid=create-repository]",
            "[data-testid=repository-name]",
            "[data-testid=create-repository-submit]",
        }
    )
    page.repository_submit_error = submit_error
    gateway = WebDriverYuqueGateway(AppSettings(session_token=SecretStr("token"), data_dir=tmp_path))

    @asynccontextmanager
    async def fake_new_page(*, visible_login: bool):
        assert visible_login is False
        yield page

    gateway._new_page = fake_new_page  # type: ignore[method-assign]

    with pytest.raises(DomainError) as error:
        await gateway.create_repository(CreateRemoteRepositoryRequest(name="SwiftUI"))

    public_error = {
        "code": error.value.code,
        "message": error.value.message,
        "status_code": error.value.status_code,
        "retryable": error.value.retryable,
        "action": error.value.action,
    }
    assert public_error == {
        "code": "YUQUE_PAGE_CHANGED",
        "message": "语雀页面响应异常，请重新登录后重试",
        "status_code": 503,
        "retryable": True,
        "action": None,
    }
    serialized_error = repr(public_error)
    assert all(private_value not in serialized_error for private_value in private_values)
    assert page.clicked.count("[data-testid=create-repository]") == 1
    assert page.fill_attempts.count(("[data-testid=repository-name]", "SwiftUI")) == 1
    assert page.clicked.count("[data-testid=create-repository-submit]") == 1
    assert len(page.screenshots) == 1
    assert page.screenshots[0].name.endswith("-create-repository.png")


async def test_gateway_uses_a_distinct_screenshot_id_for_each_operation(tmp_path: Path) -> None:
    page = FixturePage(
        {
            "[data-testid=dashboard]",
            "[data-testid=create-repository]",
            "[data-testid=repository-name]",
            "[data-testid=create-repository-submit]",
        }
    )
    page.repository_submit_error = TimeoutError("repository create response timed out")
    gateway = WebDriverYuqueGateway(
        AppSettings(session_token=SecretStr("token"), data_dir=tmp_path)
    )

    @asynccontextmanager
    async def fake_new_page(*, visible_login: bool):
        assert visible_login is False
        yield page

    gateway._new_page = fake_new_page  # type: ignore[method-assign]

    for _ in range(2):
        with pytest.raises(DomainError):
            await gateway.create_repository(CreateRemoteRepositoryRequest(name="SwiftUI"))

    filenames = [path.name for path in page.screenshots]
    assert len(filenames) == 2
    assert len(set(filenames)) == 2
    assert all(filename.endswith("-create-repository.png") for filename in filenames)


async def test_document_creation_uses_current_editor_identity_when_titles_duplicate(
    tmp_path: Path,
) -> None:
    gateway = WebDriverYuqueGateway(AppSettings(session_token=SecretStr("token"), data_dir=tmp_path))
    page = FixturePage(
        {
            "[data-testid=dashboard]",
            "[data-testid=create-document]",
            "[data-testid=editor-title]",
            "[data-testid=editor-markdown]",
            "[data-testid=editor-save]",
            "[data-testid=save-confirmation]",
            "[data-testid=document-link]",
        }
    )
    page.text["[data-testid=document-link]"] = "State"
    page.attributes[("[data-testid=document-link]", "href")] = "/swiftui/existing-state"
    page.document_url_after_save = "https://www.yuque.com/swiftui/new-state"

    @asynccontextmanager
    async def fake_new_page(*, visible_login: bool):
        assert visible_login is False
        yield page

    gateway._new_page = fake_new_page  # type: ignore[method-assign]

    created = await gateway.create_document(
        CreateRemoteDocumentRequest(repository_id="swiftui", title="State", content="# State")
    )

    assert created.remote_id == "swiftui/new-state"
    assert created.url == "https://www.yuque.com/swiftui/new-state"
    assert page.document_list_calls == 0
    assert page.clicked.count("[data-testid=editor-save]") == 1


@pytest.mark.parametrize(
    "editor_url",
    [
        "http://www.yuque.com/swiftui/new-state",
        "https://example.test/swiftui/new-state",
        "https://www.yuque.com/other/new-state",
    ],
    ids=["insecure-origin", "foreign-origin", "different-repository"],
)
async def test_document_creation_rejects_editor_url_outside_requested_yuque_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    editor_url: str,
) -> None:
    gateway = WebDriverYuqueGateway(AppSettings(session_token=SecretStr("token"), data_dir=tmp_path))
    page = FixturePage(
        {
            "[data-testid=dashboard]",
            "[data-testid=create-document]",
            "[data-testid=editor-title]",
            "[data-testid=editor-markdown]",
            "[data-testid=editor-save]",
            "[data-testid=save-confirmation]",
            "[data-testid=document-link]",
        }
    )
    page.text["[data-testid=document-link]"] = "State"
    page.attributes[("[data-testid=document-link]", "href")] = "/swiftui/existing-state"
    page.document_url_after_save = editor_url

    async def no_delay(_: float) -> None:
        return None

    monkeypatch.setattr("app.yuque.base_page.asyncio.sleep", no_delay)

    @asynccontextmanager
    async def fake_new_page(*, visible_login: bool):
        assert visible_login is False
        yield page

    gateway._new_page = fake_new_page  # type: ignore[method-assign]

    with pytest.raises(DomainError) as error:
        await gateway.create_document(
            CreateRemoteDocumentRequest(repository_id="swiftui", title="State", content="# State")
        )

    assert error.value.code == "YUQUE_PAGE_CHANGED"
    assert page.clicked.count("[data-testid=editor-save]") == 1


async def test_document_creation_confirms_current_editor_title(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = WebDriverYuqueGateway(AppSettings(session_token=SecretStr("token"), data_dir=tmp_path))
    page = FixturePage(
        {
            "[data-testid=dashboard]",
            "[data-testid=create-document]",
            "[data-testid=editor-title]",
            "[data-testid=editor-markdown]",
            "[data-testid=editor-save]",
            "[data-testid=save-confirmation]",
            "[data-testid=document-link]",
        }
    )
    page.text["[data-testid=document-link]"] = "State"
    page.attributes[("[data-testid=document-link]", "href")] = "/swiftui/existing-state"
    page.document_url_after_save = "https://www.yuque.com/swiftui/new-state"
    page.document_title_after_save = "Other State"

    async def no_delay(_: float) -> None:
        return None

    monkeypatch.setattr("app.yuque.base_page.asyncio.sleep", no_delay)

    @asynccontextmanager
    async def fake_new_page(*, visible_login: bool):
        assert visible_login is False
        yield page

    gateway._new_page = fake_new_page  # type: ignore[method-assign]

    with pytest.raises(DomainError) as error:
        await gateway.create_document(
            CreateRemoteDocumentRequest(repository_id="swiftui", title="State", content="# State")
        )

    assert error.value.code == "YUQUE_PAGE_CHANGED"
    assert page.clicked.count("[data-testid=editor-save]") == 1


async def test_document_recovery_lookup_reads_marker_without_submitting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery can discover the exact marked page without replaying create."""
    gateway = WebDriverYuqueGateway(
        AppSettings(session_token=SecretStr("token"), data_dir=tmp_path)
    )
    page = FixturePage(
        {
            "[data-testid=dashboard]",
            "[data-testid=document-link]",
            "[data-testid=editor-title]",
            "[data-testid=editor-markdown]",
        }
    )
    page.text["[data-testid=document-link]"] = "State"
    page.attributes[("[data-testid=document-link]", "href")] = "/swiftui/state"
    page.values["[data-testid=editor-title]"] = "State"
    page.values["[data-testid=editor-markdown]"] = (
        "# State\n\n<!-- docmind-mutation:recovery -->"
    )
    page.document_list_visibility_after = 1

    async def no_delay(_: float) -> None:
        return None

    monkeypatch.setattr("app.yuque.wd_gateway.asyncio.sleep", no_delay)

    @asynccontextmanager
    async def fake_new_page(*, visible_login: bool):
        assert visible_login is False
        yield page

    gateway._new_page = fake_new_page  # type: ignore[method-assign]

    found = await gateway.find_document_by_marker("swiftui", "docmind-mutation:recovery")

    assert found is not None and found.remote_id == "/swiftui/state"
    assert await gateway.document_exists("swiftui", "/swiftui/state") is True
    assert page.document_list_calls == 3
    assert page.clicked == []


@pytest.mark.parametrize(
    ("save_error", "private_values"),
    [
        (
            TimeoutError("document save response timed out"),
            ("document save response timed out",),
        ),
        (
            DomainError(
                "YUQUE_INTERNAL_SAVE_FAILURE",
                "document save failed for secret=session-cookie-123",
                418,
                True,
                "retry with session-cookie-123",
            ),
            (
                "YUQUE_INTERNAL_SAVE_FAILURE",
                "document save failed for secret=session-cookie-123",
                "session-cookie-123",
            ),
        ),
    ],
    ids=["timeout", "retryable-domain-error"],
)
async def test_document_creation_save_error_is_not_replayed(
    tmp_path: Path, save_error: Exception, private_values: tuple[str, ...]
) -> None:
    page = FixturePage(
        {
            "[data-testid=dashboard]",
            "[data-testid=create-document]",
            "[data-testid=editor-title]",
            "[data-testid=editor-markdown]",
            "[data-testid=editor-save]",
        }
    )
    page.document_save_error = save_error
    gateway = WebDriverYuqueGateway(AppSettings(session_token=SecretStr("token"), data_dir=tmp_path))

    @asynccontextmanager
    async def fake_new_page(*, visible_login: bool):
        assert visible_login is False
        yield page

    gateway._new_page = fake_new_page  # type: ignore[method-assign]

    with pytest.raises(DomainError) as error:
        await gateway.create_document(
            CreateRemoteDocumentRequest(repository_id="swiftui", title="State", content="# State")
        )

    public_error = {
        "code": error.value.code,
        "message": error.value.message,
        "status_code": error.value.status_code,
        "retryable": error.value.retryable,
        "action": error.value.action,
    }
    assert public_error == {
        "code": "YUQUE_PAGE_CHANGED",
        "message": "语雀页面响应异常，请重新登录后重试",
        "status_code": 503,
        "retryable": True,
        "action": None,
    }
    serialized_error = repr(public_error)
    assert all(private_value not in serialized_error for private_value in private_values)
    assert page.clicked.count("[data-testid=create-document]") == 1
    assert page.fill_attempts.count(("[data-testid=editor-title]", "State")) == 1
    assert page.clicked.count("[data-testid=editor-save]") == 1
    assert len(page.screenshots) == 1
    assert page.screenshots[0].name.endswith("-create-document.png")


async def test_wait_for_any_records_candidate_attempts() -> None:
    page = FixturePage({"[data-testid=fallback]"})
    base = BasePage(page, request_id="r")

    await base.wait_for_any(("testid=primary", "[data-testid=fallback]"))

    assert [attempt.selector for attempt in base._selector_attempts] == [
        "testid=primary",
        "[data-testid=fallback]",
    ]
    assert [attempt.matched for attempt in base._selector_attempts] == [False, True]
    assert base._matched_selector == "[data-testid=fallback]"


async def test_terminal_failure_writes_redacted_diagnostic_artifacts(tmp_path: Path) -> None:
    page = FixturePage(set())
    page.url = "https://www.yuque.com/team/repo/doc?query=secret"
    page.dom_html = (
        '<html><body><div data-testid="doc">secret body'
        '<a href="https://yuque.com/team/doc">title</a></div></body></html>'
    )
    base = BasePage(page, screenshots_dir=tmp_path, request_id="request", operation="create-document")

    async def fail() -> None:
        raise TimeoutError("not available")

    with pytest.raises(DomainError):
        await base.with_retry("import-markdown", fail)

    names = {path.name for path in tmp_path.iterdir()}
    assert names == {
        "request-import-markdown.png",
        "request-import-markdown.dom.html",
        "request-import-markdown.json",
    }
    dom = (tmp_path / "request-import-markdown.dom.html").read_text(encoding="utf-8")
    assert "secret body" not in dom
    assert "href" not in dom
    record = json.loads((tmp_path / "request-import-markdown.json").read_text(encoding="utf-8"))
    assert record["operation"] == "create-document"
    assert record["step"] == "import-markdown"
    assert record["page_host"] == "https://www.yuque.com"
    assert record["error_code"] == "YUQUE_PAGE_CHANGED"


async def test_create_reads_back_when_save_confirmation_is_lost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = WebDriverYuqueGateway(AppSettings(session_token=SecretStr("token"), data_dir=tmp_path))
    page = FixturePage(
        {
            "[data-testid=dashboard]",
            "[data-testid=create-document]",
            "[data-testid=editor-title]",
            "[data-testid=editor-markdown]",
            "[data-testid=editor-save]",
        }
    )

    async def no_delay(_: float) -> None:
        return None

    monkeypatch.setattr("app.yuque.base_page.asyncio.sleep", no_delay)

    @asynccontextmanager
    async def fake_new_page(*, visible_login: bool):
        assert visible_login is False
        yield page

    gateway._new_page = fake_new_page  # type: ignore[method-assign]

    async def fake_find(repository_id: str, marker: str) -> RemoteDocument:
        assert marker == "docmind-mutation:create-1"
        return RemoteDocument(
            remote_id="swiftui/new-state",
            repository_id=repository_id,
            title="State",
            url="https://www.yuque.com/swiftui/new-state",
        )

    gateway.find_document_by_marker = fake_find  # type: ignore[method-assign]

    created = await gateway.create_document(
        CreateRemoteDocumentRequest(
            repository_id="swiftui",
            title="State",
            content="# State\n\n<!-- docmind-mutation:create-1 -->",
        )
    )
    assert created.remote_id == "swiftui/new-state"
    assert page.clicked.count("[data-testid=editor-save]") == 1


async def test_update_reads_back_by_marker_when_confirmation_is_lost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = WebDriverYuqueGateway(AppSettings(session_token=SecretStr("token"), data_dir=tmp_path))
    page = FixturePage(
        {
            "[data-testid=dashboard]",
            "[data-testid=editor-title]",
            "[data-testid=editor-markdown]",
            "[data-testid=editor-save]",
        }
    )

    async def no_delay(_: float) -> None:
        return None

    monkeypatch.setattr("app.yuque.base_page.asyncio.sleep", no_delay)

    @asynccontextmanager
    async def fake_new_page(*, visible_login: bool):
        assert visible_login is False
        yield page

    gateway._new_page = fake_new_page  # type: ignore[method-assign]

    async def fake_read(document_id: str, *, strip_mutation_marker: bool) -> RemoteDocumentContent:
        del strip_mutation_marker
        return RemoteDocumentContent(
            remote_id=document_id,
            repository_id="swiftui",
            title="State",
            content="# State\n\n<!-- docmind-mutation:update-1 -->",
            url="https://www.yuque.com/swiftui/state",
        )

    gateway._read_document = fake_read  # type: ignore[method-assign]

    updated = await gateway.update_document(
        UpdateRemoteDocumentRequest(
            document_id="swiftui/state",
            title="State",
            content="# State\n\n<!-- docmind-mutation:update-1 -->",
        )
    )
    assert updated.remote_id == "swiftui/state"


async def test_delete_treats_already_gone_as_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = WebDriverYuqueGateway(AppSettings(session_token=SecretStr("token"), data_dir=tmp_path))
    page = FixturePage(
        {
            "[data-testid=dashboard]",
            "[data-testid=delete-document]",
            "[data-testid=confirm-delete]",
        }
    )

    async def no_delay(_: float) -> None:
        return None

    monkeypatch.setattr("app.yuque.base_page.asyncio.sleep", no_delay)

    @asynccontextmanager
    async def fake_new_page(*, visible_login: bool):
        assert visible_login is False
        yield page

    gateway._new_page = fake_new_page  # type: ignore[method-assign]

    async def fake_exists(repository_id: str, document_id: str) -> bool:
        return False

    gateway.document_exists = fake_exists  # type: ignore[method-assign]

    await gateway.delete_document("swiftui/state", "swiftui")
    assert page.clicked.count("[data-testid=confirm-delete]") >= 1




class TestBrowserSelectionPrefersSystemInstall:
    """Regression: the browser must come from the machine, never from us.

    The Playwright gateway always asked for its own bundled Chromium, so every
    Yuque login failed with YUQUE_BROWSER_UNAVAILABLE on a machine that already
    had Chrome, and users were pushed into a ~170 MB download they did not need.
    The replacement keeps the same promise in a different place: it drives an
    installed browser, and when there is none it says so instead of silently
    fetching a browser of its own.
    """

    def test_a_missing_browser_is_reported_not_downloaded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for empty in ("_darwin_candidates", "_windows_candidates", "_linux_candidates"):
            monkeypatch.setattr(browser_module, empty, list)

        with pytest.raises(browser_module.BrowserNotFoundError) as raised:
            browser_module.find_browser()

        message = str(raised.value)
        assert "Chrome" in message
        assert "Chromium" in message
        # The wording has to tell the user what to install; a bare failure
        # would read as "the app is broken".
        assert "请安装" in message

    def test_an_installed_browser_is_used_without_any_download(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One candidate, one probe, no network and no install step."""
        monkeypatch.setattr(
            browser_module,
            "_darwin_candidates",
            lambda: [("chrome", "/apps/Google Chrome.app/.../Google Chrome")],
        )
        # The other platforms contribute no candidates on this machine; the
        # search must therefore be decided by the darwin list alone.
        monkeypatch.setattr(browser_module, "_windows_candidates", list)
        monkeypatch.setattr(browser_module, "_linux_candidates", list)
        monkeypatch.setattr(
            browser_module, "_read_major_version", lambda _path: 154
        )

        found = browser_module.find_browser()

        assert found.kind == "chrome"
        assert found.major_version == 154
        # The executable is used as found — no bundled path is substituted.
        assert found.executable.endswith("Google Chrome")

    def test_candidates_are_tried_in_order_until_one_reports_a_version(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unreadable or versionless binary must not win the search.

        This is what the old channel fallback tested, in the place where the
        choice is now made: the candidate list, not a browser launch.
        """
        candidates = [
            ("chrome", "/apps/broken/Google Chrome"),
            ("chromium", "/apps/Chromium.app/.../Chromium"),
        ]
        probed: list[str] = []

        def fake_version(path: str) -> int | None:
            probed.append(path)
            return 153 if "Chromium" in path else None

        monkeypatch.setattr(browser_module, "_darwin_candidates", lambda: list(candidates))
        monkeypatch.setattr(browser_module, "_windows_candidates", list)
        monkeypatch.setattr(browser_module, "_linux_candidates", list)
        monkeypatch.setattr(browser_module, "_read_major_version", fake_version)

        found = browser_module.find_browser()

        # The unusable first candidate was tried and skipped...
        assert probed == ["/apps/broken/Google Chrome", "/apps/Chromium.app/.../Chromium"]
        # ...and the second one, which reported a version, was returned.
        assert found.kind == "chromium"
        assert found.major_version == 153
