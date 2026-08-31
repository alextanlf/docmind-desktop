from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.api.errors import DomainError
from app.config import AppSettings
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
        del state, timeout
        if not self.visible:
            raise TimeoutError(self.selector)

    async def click(self) -> None:
        if not self.visible:
            raise TimeoutError(self.selector)
        self.page.clicked.append(self.selector)

    async def fill(self, value: str) -> None:
        if not self.visible:
            raise TimeoutError(self.selector)
        self.page.filled[self.selector] = value

    async def all(self) -> list[FixtureLocator]:
        return [self] if self.visible else []

    async def inner_text(self) -> str:
        return self.page.text.get(self.selector, "")

    async def get_attribute(self, name: str) -> str | None:
        return self.page.attributes.get((self.selector, name))


class FixturePage:
    def __init__(self, available: set[str], fixture: Path | None = None) -> None:
        self.available = available
        self.fixture = fixture
        self.clicked: list[str] = []
        self.filled: dict[str, str] = {}
        self.text: dict[str, str] = {}
        self.attributes: dict[tuple[str, str], str] = {}
        self.screenshots: list[Path] = []
        self.gotos: list[str] = []
        self.url = "about:blank"

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

    async def goto(self, url: str, wait_until: str = "load") -> None:
        del wait_until
        self.url = url
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
