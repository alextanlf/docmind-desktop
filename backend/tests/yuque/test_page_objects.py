from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.api.errors import DomainError
from app.config import AppSettings
from app.schemas.yuque import (
    CreateRepositoryRequest,
    CreateYuqueDocumentRequest,
    UpdateYuqueDocumentRequest,
)
from app.yuque.base_page import BasePage
from app.yuque.dashboard_page import DashboardPage
from app.yuque.editor_page import EditorPage
from app.yuque.gateway import PlaywrightYuqueGateway
from app.yuque.login_page import LoginPage


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


class FixturePage:
    def __init__(self, available: set[str], fixture: Path | None = None) -> None:
        self.available = available
        self.fixture = fixture
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
        self.goto_attempts: list[str] = []
        self.document_list_visibility_after: int | None = None
        self.document_list_calls = 0
        self.repository_list_visibility_after: int | None = None
        self.repository_list_calls = 0
        self.repository_submit_error: Exception | None = None

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

    async def goto(self, url: str, wait_until: str = "load") -> None:
        del wait_until
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
    gateway = PlaywrightYuqueGateway(settings)
    page = FixturePage({"[data-testid=dashboard]"})

    @asynccontextmanager
    async def fake_new_page(*, visible_login: bool):
        assert visible_login is False
        yield page

    gateway._new_page = fake_new_page  # type: ignore[method-assign]

    status = await gateway.login_status()

    assert gateway._playwright is None
    assert page.gotos == ["https://www.yuque.com/dashboard"]
    assert status.logged_in is True


async def test_real_gateway_reports_logged_out_status_without_raising(tmp_path: Path) -> None:
    settings = AppSettings(session_token=SecretStr("token"), data_dir=tmp_path)
    gateway = PlaywrightYuqueGateway(settings)
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


async def test_real_gateway_maps_expired_cookie_to_login_required(tmp_path: Path) -> None:
    settings = AppSettings(session_token=SecretStr("token"), data_dir=tmp_path)
    gateway = PlaywrightYuqueGateway(settings)
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
    gateway = PlaywrightYuqueGateway(settings)
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
    gateway = PlaywrightYuqueGateway(settings)
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


async def test_close_stops_playwright_once_when_called_concurrently(tmp_path: Path) -> None:
    class FakeManager:
        def __init__(self) -> None:
            self.stops = 0

        async def stop(self) -> None:
            self.stops += 1
            await asyncio.sleep(0)

    gateway = PlaywrightYuqueGateway(AppSettings(session_token=SecretStr("token"), data_dir=tmp_path))
    manager = FakeManager()
    gateway._playwright = manager

    await asyncio.gather(gateway.close(), gateway.close())

    assert manager.stops == 1
    assert gateway._playwright is None


async def test_begin_login_waits_for_regular_context_work_instead_of_reporting_conflict(
    tmp_path: Path,
) -> None:
    gateway = PlaywrightYuqueGateway(AppSettings(session_token=SecretStr("token"), data_dir=tmp_path))
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
    gateway = PlaywrightYuqueGateway(AppSettings(session_token=SecretStr("token"), data_dir=tmp_path))
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
        UpdateYuqueDocumentRequest(document_id="swiftui/state", title="State 2", content="# State 2")
    )

    assert (read.repository_id, read.title) == ("swiftui", "State")
    assert (updated.repository_id, updated.title) == ("swiftui", "State 2")


async def test_create_missing_document_and_delete_confirmation_are_retryable(tmp_path: Path) -> None:
    gateway = PlaywrightYuqueGateway(AppSettings(session_token=SecretStr("token"), data_dir=tmp_path))
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
            CreateYuqueDocumentRequest(repository_id="swiftui", title="State", content="# State")
        )
    await gateway.delete_document("swiftui/state")

    assert error.value.code == "YUQUE_PAGE_CHANGED"
    assert "[data-testid=delete-confirmation]" in page.waited_selectors
    assert page.clicked[-2:] == ["[data-testid=delete-document]", "[data-testid=confirm-delete]"]


async def test_dashboard_without_stable_selector_maps_to_page_changed(tmp_path: Path) -> None:
    gateway = PlaywrightYuqueGateway(AppSettings(session_token=SecretStr("token"), data_dir=tmp_path))
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
    gateway = PlaywrightYuqueGateway(AppSettings(session_token=SecretStr("token"), data_dir=tmp_path))
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
    gateway = PlaywrightYuqueGateway(AppSettings(session_token=SecretStr("token"), data_dir=tmp_path))

    async def no_delay(_: float) -> None:
        return None

    monkeypatch.setattr("app.yuque.base_page.asyncio.sleep", no_delay)

    @asynccontextmanager
    async def fake_new_page(*, visible_login: bool):
        assert visible_login is False
        yield page

    gateway._new_page = fake_new_page  # type: ignore[method-assign]

    created = await gateway.create_repository(CreateRepositoryRequest(name="SwiftUI"))

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
    gateway = PlaywrightYuqueGateway(AppSettings(session_token=SecretStr("token"), data_dir=tmp_path))

    @asynccontextmanager
    async def fake_new_page(*, visible_login: bool):
        assert visible_login is False
        yield page

    gateway._new_page = fake_new_page  # type: ignore[method-assign]

    with pytest.raises(DomainError) as error:
        await gateway.create_repository(CreateRepositoryRequest(name="SwiftUI"))

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
    assert [path.name for path in page.screenshots] == [
        f"{gateway._request_id}-create-repository.png"
    ]


async def test_document_creation_retries_visibility_without_saving_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = PlaywrightYuqueGateway(AppSettings(session_token=SecretStr("token"), data_dir=tmp_path))
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
    page.document_list_visibility_after = 1

    async def no_delay(_: float) -> None:
        return None

    monkeypatch.setattr("app.yuque.base_page.asyncio.sleep", no_delay)

    @asynccontextmanager
    async def fake_new_page(*, visible_login: bool):
        assert visible_login is False
        yield page

    gateway._new_page = fake_new_page  # type: ignore[method-assign]

    created = await gateway.create_document(
        CreateYuqueDocumentRequest(repository_id="swiftui", title="State", content="# State")
    )

    assert created.title == "State"
    assert page.document_list_calls == 2
    assert page.clicked.count("[data-testid=editor-save]") == 1
