from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import quote, urlparse
from uuid import uuid4

from playwright.async_api import BrowserContext, async_playwright
from playwright.async_api import Error as PlaywrightError

from app.api.errors import DomainError
from app.config import AppSettings
from app.document.parser import DocumentParser
from app.remote.markers import extract_mutation_marker, strip_mutation_marker
from app.remote.provider import ProviderCapabilities, ProviderIdentity
from app.remote.redaction import mask_account
from app.schemas.imports import DownloadedDocument
from app.schemas.remote import (
    BrowserInstallResult,
    CreateRemoteDocumentRequest,
    CreateRemoteRepositoryRequest,
    LoginResult,
    LoginStatus,
    RemoteDocument,
    RemoteDocumentContent,
    RemoteRepository,
    UpdateRemoteDocumentRequest,
)
from app.yuque.base_page import RETRY_DELAYS
from app.yuque.dashboard_page import DashboardPage
from app.yuque.editor_page import EditorPage
from app.yuque.login_page import LoginPage
from app.yuque.repository_page import RepositoryPage

# 语雀页面依赖 CDN 上的阻塞脚本，网络异常（例如 IPv6 路由不通）时页面可能
# 永远到不了 domcontentloaded。登录状态探测必须有自己的时间预算，不能把
# 桌面端启动流程挂在那里等。
# 任何语雀页面操作都不允许无限等待：导航和"页面壳是否渲染出来"各自有预算，
# 否则一次网络异常会长时间占住串行浏览器锁，拖死整个应用启动流程。
_PAGE_NAVIGATION_TIMEOUT_MS = 15_000
_PAGE_RENDER_TIMEOUT_MS = 8_000
_LOGIN_STATUS_NAV_TIMEOUT_MS = 6_000
_LOGIN_STATUS_RENDER_TIMEOUT_MS = 3_000
_LOGIN_STATUS_SETTLE_SECONDS = 1.5
_LOGIN_STATUS_POLL_SECONDS = 0.25
_LOGIN_STATUS_SELECTOR_TIMEOUT_MS = 300
_YUQUE_SESSION_COOKIE_NAMES = ("_yuque_session", "yuque_ctoken")



class PlaywrightYuqueGateway:
    """Serialized persistent-profile Playwright gateway for real Yuque access."""

    identity = ProviderIdentity(
        name="yuque",
        label="语雀",
        capabilities=ProviderCapabilities(browser_install=True, marker_lookup=True),
    )

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.read_calls: list[str] = []
        self.write_calls: list[str] = []
        self._context_lock = asyncio.Lock()
        self._login_lock = asyncio.Lock()
        self._playwright: Any | None = None

    async def login_status(self) -> LoginStatus:
        """Best-effort login probe with a hard time budget.

        The Yuque shell is rendered by blocking scripts served from a CDN.  When
        those requests stall (offline CDN, broken IPv6 route), ``domcontentloaded``
        never fires, so the probe commits the navigation, polls briefly for a
        decisive signal and finally falls back to the session cookie instead of
        blocking the desktop app for minutes.
        """
        request_id = uuid4().hex
        try:
            async with self._new_page(visible_login=False) as page:
                login = LoginPage(page, self.settings.screenshots_dir, request_id)
                await page.goto(
                    "https://www.yuque.com/dashboard",
                    wait_until="commit",
                    timeout=_LOGIN_STATUS_NAV_TIMEOUT_MS,
                )
                rendered = await _wait_for_render(page, _LOGIN_STATUS_RENDER_TIMEOUT_MS)
                deadline = time.monotonic() + _LOGIN_STATUS_SETTLE_SECONDS
                while True:
                    if _is_login_url(page.url):
                        return LoginStatus(logged_in=False, account_label=None, requires_login=True)
                    if await login.is_logged_in(timeout=_LOGIN_STATUS_SELECTOR_TIMEOUT_MS):
                        return LoginStatus(
                            logged_in=True,
                            account_label=mask_account(await login.account_label()),
                            requires_login=False,
                        )
                    if time.monotonic() >= deadline:
                        break
                    await asyncio.sleep(_LOGIN_STATUS_POLL_SECONDS)
                if rendered:
                    raise DomainError(
                        "YUQUE_PAGE_CHANGED",
                        "语雀页面结构已变化，请重新登录后重试",
                        503,
                        True,
                    )
                if await _has_yuque_session_cookie(page):
                    return LoginStatus(logged_in=True, account_label=None, requires_login=False)
                return LoginStatus(logged_in=False, account_label=None, requires_login=True)
        except PlaywrightError:
            return LoginStatus(logged_in=False, account_label=None, requires_login=True)

    async def begin_login(self) -> LoginResult:
        if self._login_lock.locked():
            raise DomainError("YUQUE_LOGIN_IN_PROGRESS", "语雀登录正在进行", 409, False)
        request_id = uuid4().hex
        try:
            async with self._login_lock, self._new_page(visible_login=True) as page:
                login = LoginPage(page, self.settings.screenshots_dir, request_id)

                async def open_login_page() -> None:
                    await page.goto("https://www.yuque.com/login", wait_until="domcontentloaded")

                await login.with_retry("open-login", open_login_page)
                if not await login.wait_until_logged_in(timeout=600_000):
                    await login._capture_failure("login-timeout")
                    raise DomainError(
                        "YUQUE_LOGIN_REQUIRED",
                        "语雀登录超时或已取消",
                        408,
                        True,
                        "重新登录语雀",
                        auth_expired=True,
                    )
                return LoginResult(
                    logged_in=True,
                    account_label=mask_account(await login.account_label()),
                    requires_login=False,
                )
        except DomainError:
            raise
        except PlaywrightError:
            raise DomainError(
                "YUQUE_BROWSER_UNAVAILABLE",
                "本机缺少语雀登录浏览器，请先安装 Playwright Chromium",
                503,
                True,
                "安装后重试",
            ) from None

    async def install_browser(self) -> BrowserInstallResult:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "playwright",
            "install",
            "chromium",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await process.communicate()
        installed = process.returncode == 0
        message = (
            "语雀浏览器已安装"
            if installed
            else f"浏览器安装失败：{(output or b'').decode(errors='replace').strip()[:300]}"
        )
        return BrowserInstallResult(installed=installed, message=message)

    async def list_repositories(self) -> list[RemoteRepository]:
        async with self._background_page("list-repositories") as operation:
            page, request_id = operation
            return await DashboardPage(page, self.settings.screenshots_dir, request_id).with_retry(
                "list-repositories", DashboardPage(page).list_repositories
            )

    async def create_repository(self, request: CreateRemoteRepositoryRequest) -> RemoteRepository:
        self.write_calls.append("create_repository")
        async with self._background_page("create-repository") as operation:
            page, request_id = operation
            dashboard = DashboardPage(page, self.settings.screenshots_dir, request_id)
            try:
                await dashboard.submit_new_repository(request.name)
            except DomainError as error:
                if not error.retryable:
                    raise
                await dashboard._capture_failure("create-repository")
                raise DomainError(
                    "YUQUE_PAGE_CHANGED", "语雀页面响应异常，请重新登录后重试", 503, True
                ) from None
            except (PlaywrightError, TimeoutError, ConnectionError, OSError):
                await dashboard._capture_failure("create-repository")
                raise DomainError(
                    "YUQUE_PAGE_CHANGED", "语雀页面响应异常，请重新登录后重试", 503, True
                ) from None
            return await dashboard.with_retry(
                "confirm-created-repository", lambda: dashboard.find_repository(request.name)
            )

    async def list_documents(self, repository_id: str) -> list[RemoteDocument]:
        async with self._background_page("list-documents") as operation:
            page, request_id = operation
            repository = RepositoryPage(page, self.settings.screenshots_dir, request_id)

            async def list_documents() -> list[RemoteDocument]:
                await _open_yuque_resource(page, repository_id)
                return await repository.list_documents(repository_id)

            return await repository.with_retry("list-documents", list_documents)

    async def create_document(self, request: CreateRemoteDocumentRequest) -> RemoteDocument:
        self.write_calls.append("create_document")
        marker = _extract_mutation_marker(request.content)
        try:
            async with self._background_page("create-document") as operation:
                page, request_id = operation
                repository = RepositoryPage(page, self.settings.screenshots_dir, request_id, operation="create-document")
                editor = EditorPage(page, self.settings.screenshots_dir, request_id, operation="create-document")

                async def create() -> None:
                    await _open_yuque_resource(page, request.repository_id)
                    await repository.open_new_document()
                    await editor.set_title(request.title)
                    await editor.import_markdown(request.content)

                async def confirm_created_document() -> RemoteDocument:
                    parsed_url = urlparse(page.url)
                    repository_id = _repository_id_from_document_url(page.url)
                    document_id = _resource_identity(page.url)
                    requested_repository_id = _resource_identity(request.repository_id)
                    if (
                        parsed_url.scheme != "https"
                        or parsed_url.hostname != "www.yuque.com"
                        or parsed_url.username is not None
                        or parsed_url.password is not None
                        or repository_id != requested_repository_id
                        or document_id == requested_repository_id
                    ):
                        raise DomainError("YUQUE_PAGE_CHANGED", "新建文档后未找到文档，请重新登录后重试", 503, True)
                    title = await editor.read_title()
                    if title != request.title:
                        raise DomainError("YUQUE_PAGE_CHANGED", "新建文档后未找到文档，请重新登录后重试", 503, True)
                    return RemoteDocument(
                        remote_id=document_id,
                        repository_id=request.repository_id,
                        title=title,
                        url=page.url,
                    )

                try:
                    await create()
                except DomainError as error:
                    if not error.retryable:
                        raise
                    await editor._capture_failure("create-document", error.code)
                    raise DomainError(
                        "YUQUE_PAGE_CHANGED", "语雀页面响应异常，请重新登录后重试", 503, True
                    ) from None
                except (PlaywrightError, TimeoutError, ConnectionError, OSError):
                    await editor._capture_failure("create-document")
                    raise DomainError(
                        "YUQUE_PAGE_CHANGED", "语雀页面响应异常，请重新登录后重试", 503, True
                    ) from None
                return await editor.with_retry("confirm-created-document", confirm_created_document)
        except DomainError as error:
            if error.retryable and marker is not None:
                discovered = await self.find_document_by_marker(request.repository_id, marker)
                if discovered is not None:
                    return discovered
            raise
        except (PlaywrightError, TimeoutError, ConnectionError, OSError):
            raise DomainError("YUQUE_PAGE_CHANGED", "语雀页面响应异常，请重新登录后重试", 503, True) from None

    async def find_document_by_marker(
        self, repository_id: str, marker: str
    ) -> RemoteDocument | None:
        for delay in (*RETRY_DELAYS, None):
            documents = await self.list_documents(repository_id)
            for document in documents:
                content = await self._read_document(document.remote_id, strip_mutation_marker=False)
                if marker in content.content:
                    return document
            if delay is None:
                return None
            await asyncio.sleep(delay)
        return None

    async def document_exists(self, repository_id: str, document_id: str) -> bool:
        documents = await self.list_documents(repository_id)
        target = _resource_identity(document_id)
        return any(
            target in {_resource_identity(document.remote_id), _resource_identity(document.url)}
            for document in documents
        )

    async def read_document(self, document_id: str) -> RemoteDocumentContent:
        self.read_calls.append("read_document")
        return await self._read_document(document_id, strip_mutation_marker=True)

    async def _read_document(
        self, document_id: str, *, strip_mutation_marker: bool
    ) -> RemoteDocumentContent:
        async with self._background_page("read-document") as operation:
            page, request_id = operation
            editor = EditorPage(page, self.settings.screenshots_dir, request_id)

            async def read() -> RemoteDocumentContent:
                await _open_yuque_resource(page, document_id)
                api_document = await self._read_document_via_api(page, document_id)
                if api_document is not None:
                    if strip_mutation_marker:
                        api_document.content = _strip_mutation_marker(api_document.content)
                    return api_document
                content = await editor.read_markdown()
                if strip_mutation_marker:
                    content = _strip_mutation_marker(content)
                return RemoteDocumentContent(
                    remote_id=document_id,
                    repository_id=_repository_id_from_document_url(page.url),
                    title=await editor.read_title(),
                    content=content,
                    url=page.url,
                )

            return await editor.with_retry("read-document", read)

    async def _read_document_via_api(
        self, page: Any, document_id: str
    ) -> RemoteDocumentContent | None:
        if not hasattr(page, "evaluate") or not hasattr(page, "request"):
            return None
        slug = await page.evaluate(
            "window.appData?.doc?.slug || location.pathname.split('/').filter(Boolean).pop()"
        )
        book_id = await page.evaluate(
            "window.appData?.doc?.book_id || window.appData?.book?.id"
        )
        if not slug or not book_id:
            return None
        url = (
            f"https://www.yuque.com/api/docs/{slug}"
            "?include_contributors=true&include_like=true&include_hits=true"
            f"&merge_dynamic_data=false&book_id={book_id}"
        )
        response = await page.request.get(url)
        if not response.ok:
            return None
        try:
            payload = await response.json()
            data = payload.get("data", {}) if isinstance(payload, dict) else {}
        except Exception:  # noqa: BLE001 - malformed API response falls back to DOM
            return None
        content = data.get("content")
        if not isinstance(content, str) or not content.strip():
            return None
        title = data.get("title") or await page.title()
        parsed = DocumentParser().parse(
            DownloadedDocument(
                title=str(title),
                source_url=page.url,
                media_type="text/html",
                raw_bytes=content.encode("utf-8"),
            )
        )
        return RemoteDocumentContent(
            remote_id=document_id,
            repository_id=_repository_id_from_document_url(page.url),
            title=str(title),
            content=parsed.markdown,
            url=page.url,
        )

    async def update_document(self, request: UpdateRemoteDocumentRequest) -> RemoteDocument:
        self.write_calls.append("update_document")
        marker = _extract_mutation_marker(request.content)
        try:
            async with self._background_page("update-document") as operation:
                page, request_id = operation
                editor = EditorPage(page, self.settings.screenshots_dir, request_id, operation="update-document")

                async def update() -> RemoteDocument:
                    await _open_yuque_resource(page, request.document_id)
                    await editor.set_title(request.title)
                    await editor.import_markdown(request.content)
                    return RemoteDocument(
                        remote_id=request.document_id,
                        repository_id=_repository_id_from_document_url(page.url),
                        title=await editor.read_title(),
                        url=page.url,
                    )

                return await editor.with_retry("update-document", update)
        except DomainError as error:
            if error.retryable and marker is not None:
                current = await self._read_document(request.document_id, strip_mutation_marker=False)
                if marker in current.content:
                    return RemoteDocument(
                        remote_id=request.document_id,
                        repository_id=current.repository_id,
                        title=current.title,
                        url=current.url,
                    )
            raise
        except (PlaywrightError, TimeoutError, ConnectionError, OSError):
            raise DomainError("YUQUE_PAGE_CHANGED", "语雀页面响应异常，请重新登录后重试", 503, True) from None

    async def delete_document(self, document_id: str, repository_id: str) -> None:
        self.write_calls.append("delete_document")
        try:
            async with self._background_page("delete-document") as operation:
                page, request_id = operation
                repository = RepositoryPage(page, self.settings.screenshots_dir, request_id, operation="delete-document")

                async def delete() -> None:
                    await _open_yuque_resource(page, document_id)
                    await repository.delete_current_document()

                await repository.with_retry("delete-document", delete)
        except DomainError as error:
            if error.retryable and not await self.document_exists(repository_id, document_id):
                return
            raise
        except (PlaywrightError, TimeoutError, ConnectionError, OSError):
            raise DomainError("YUQUE_PAGE_CHANGED", "语雀页面响应异常，请重新登录后重试", 503, True) from None

    async def close(self) -> None:
        async with self._context_lock:
            manager = self._playwright
            self._playwright = None
            if manager is not None:
                await manager.stop()

    @asynccontextmanager
    async def _background_page(self, operation: str) -> AsyncIterator[tuple[Any, str]]:
        request_id = uuid4().hex
        async with self._new_page(visible_login=False) as page:
            login = LoginPage(page, self.settings.screenshots_dir, request_id)

            async def authenticate() -> None:
                await page.goto(
                    "https://www.yuque.com/dashboard",
                    wait_until="commit",
                    timeout=_PAGE_NAVIGATION_TIMEOUT_MS,
                )
                if not await _wait_for_render(page, _PAGE_RENDER_TIMEOUT_MS):
                    raise DomainError(
                        "YUQUE_PAGE_UNAVAILABLE",
                        "语雀页面加载超时，请检查网络后重试",
                        503,
                        False,
                        "检查网络后重试",
                    )
                if await login.is_logged_in():
                    return
                if _is_login_url(page.url):
                    raise DomainError(
                        "YUQUE_LOGIN_REQUIRED",
                        "语雀登录已失效，请重新登录",
                        401,
                        False,
                        "重新登录语雀",
                        auth_expired=True,
                    )
                raise DomainError("YUQUE_PAGE_CHANGED", "语雀页面结构已变化，请重新登录后重试", 503, True)

            await login.with_retry(f"{operation}-authenticate", authenticate)
            yield page, request_id

    @asynccontextmanager
    async def _new_page(self, visible_login: bool) -> AsyncIterator[Any]:
        async with self._context_lock:
            context: BrowserContext | None = None
            try:
                profile = self.settings.browser_data_dir
                profile.mkdir(mode=0o700, parents=True, exist_ok=True)
                if os.name != "nt":
                    profile.chmod(0o700)
                if self._playwright is None:
                    self._playwright = await async_playwright().start()
                context = await self._playwright.chromium.launch_persistent_context(
                    str(profile),
                    headless=not visible_login,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
                page = context.pages[0] if context.pages else await context.new_page()
                setter = getattr(page, "set_default_navigation_timeout", None)
                if setter is not None:
                    setter(_PAGE_NAVIGATION_TIMEOUT_MS)
                yield page
            finally:
                if context is not None:
                    await context.close()


async def _wait_for_render(page: Any, timeout_ms: int) -> bool:
    """True when the page actually reached ``domcontentloaded``.

    A rendered page that still lacks every login marker means the Yuque DOM moved
    and is worth surfacing; a page that never finished loading is a network
    problem and must not be reported as a structure change.
    """
    waiter = getattr(page, "wait_for_load_state", None)
    if waiter is None:
        return False
    try:
        await waiter("domcontentloaded", timeout=timeout_ms)
    except PlaywrightError:
        return False
    return True


async def _has_yuque_session_cookie(page: Any) -> bool:
    """True when the persistent profile still holds a Yuque session cookie.

    Yuque keeps the login in ``_yuque_session``; the cookie stays valid even when
    the page itself cannot finish rendering.
    """
    context = getattr(page, "context", None)
    cookies = getattr(context, "cookies", None)
    if cookies is None:
        return False
    try:
        stored = await cookies("https://www.yuque.com")
    except (PlaywrightError, OSError):
        return False
    return any(
        isinstance(cookie, dict)
        and cookie.get("name") in _YUQUE_SESSION_COOKIE_NAMES
        and cookie.get("value")
        for cookie in stored
    )




async def _open_yuque_resource(page: Any, resource_id: str) -> None:
    url = resource_id if resource_id.startswith("https://www.yuque.com/") else (
        f"https://www.yuque.com/{quote(resource_id.strip('/'), safe='/')}"
    )
    await page.goto(url, wait_until="domcontentloaded")


def _is_login_url(url: str) -> bool:
    return urlparse(url).path.rstrip("/") == "/login"


def _repository_id_from_document_url(url: str) -> str:
    path = urlparse(url).path.strip("/")
    repository_id, separator, _ = path.rpartition("/")
    return repository_id if separator else ""


def _resource_identity(value: str | None) -> str:
    if not value:
        return ""
    parsed = urlparse(value)
    return (parsed.path or value).strip("/")


# The mutation marker helpers live in app.remote.markers; keep the historical
# private names used inside this module as aliases.
_strip_mutation_marker = strip_mutation_marker
_extract_mutation_marker = extract_mutation_marker
