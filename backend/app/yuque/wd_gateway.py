"""Yuque gateway driven by WebDriver against the user's own Chrome.

The browser is the one already installed on the machine and the driver is a
~9 MB binary fetched on demand, instead of a 133 MB Node runtime plus a
170 MB Chromium bundled into the installer.

The nine provider capabilities came over verbatim from the Playwright gateway
this replaced: they only ever touch the page facade (``goto`` / ``locator`` /
``url`` …) and the shared page objects, so a selector or an error mapping was
never written twice. Only the three driver-level methods are written out
below.

``_read_document_via_api`` keeps working unchanged: it guards on
``hasattr(page, "evaluate")`` and falls back to reading the rendered DOM, which
is exactly what happens on this stack.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from app.api.errors import DomainError
from app.config import AppSettings
from app.document.parser import DocumentParser
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
from app.yuque.codes import YUQUE_BROWSER_UNAVAILABLE_CODE
from app.yuque.dashboard_page import DashboardPage
from app.yuque.editor_page import EditorPage
from app.yuque.login_page import LoginPage
from app.yuque.navigation import (
    _extract_mutation_marker,
    _is_login_url,
    _open_yuque_resource,
    _repository_id_from_document_url,
    _resource_identity,
    _strip_mutation_marker,
    _wait_for_render,
)
from app.yuque.repository_page import RepositoryPage
from app.yuque.wd_locator import _RETRYABLE as _WEBDRIVER_RETRYABLE
from app.yuque.wd_page import WdPage
from app.yuque.wd_session import (
    YuqueBrowserUnavailableError,
    has_stored_session,
    open_session,
    persist_session,
)

# Budgets for a background capability call: reach the dashboard, then give
# the SPA a moment to render before declaring the network at fault.
_PAGE_NAVIGATION_TIMEOUT_MS = 15_000
_PAGE_RENDER_TIMEOUT_MS = 8_000

_LOGIN_STATUS_NAV_TIMEOUT_MS = 6_000
_LOGIN_STATUS_RENDER_TIMEOUT_MS = 3_000
_LOGIN_STATUS_SETTLE_SECONDS = 1.5
_LOGIN_STATUS_POLL_SECONDS = 0.25
_LOGIN_STATUS_SELECTOR_TIMEOUT_MS = 300
_LOGIN_WAIT_SECONDS = 600.0


class WebDriverYuqueGateway:
    """WebDriver-backed Yuque gateway, serialised one session at a time."""

    identity = ProviderIdentity(
        name="yuque",
        label="语雀",
        capabilities=ProviderCapabilities(
            browser_install=True,
            marker_lookup=True,
            browser_unavailable_code=YUQUE_BROWSER_UNAVAILABLE_CODE,
        ),
    )

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.read_calls: list[str] = []
        self.write_calls: list[str] = []
        self._context_lock = asyncio.Lock()
        self._login_lock = asyncio.Lock()

    @property
    def _cache_root(self):
        return self.settings.data_dir

    @property
    def _profile_dir(self):
        return self.settings.browser_data_dir

    # -- login ------------------------------------------------------------

    async def login_status(self) -> LoginStatus:
        """Best-effort probe with a hard budget.

        Mirrors the previous gateway: commit the navigation, poll briefly for
        a decisive signal, and fall back to the stored session cookie so a slow
        CDN cannot stall the desktop app.
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
                        return LoginStatus(
                            logged_in=False, account_label=None, requires_login=True
                        )
                    if await login.is_logged_in(
                        timeout=_LOGIN_STATUS_SELECTOR_TIMEOUT_MS
                    ):
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
                if has_stored_session(self._cache_root):
                    return LoginStatus(
                        logged_in=True, account_label=None, requires_login=False
                    )
                return LoginStatus(
                    logged_in=False, account_label=None, requires_login=True
                )
        except YuqueBrowserUnavailableError:
            # No usable browser is a "not signed in" answer for this probe, not
            # an error: the desktop app asks on every start and must not raise.
            return LoginStatus(logged_in=False, account_label=None, requires_login=True)
        except (*_WEBDRIVER_RETRYABLE, TimeoutError, ConnectionError, OSError):
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
                if not await login.wait_until_logged_in(
                    timeout=int(_LOGIN_WAIT_SECONDS * 1000)
                ):
                    await login._capture_failure("login-timeout")
                    raise DomainError(
                        "YUQUE_LOGIN_REQUIRED",
                        "语雀登录超时或已取消",
                        408,
                        True,
                        "重新登录语雀",
                        auth_expired=True,
                    )
                # The windowed session is the only place the cookie exists;
                # persist it here or the next launch would ask again.
                persist_session(page.session, self._cache_root)
                return LoginResult(
                    logged_in=True,
                    account_label=mask_account(await login.account_label()),
                    requires_login=False,
                )
        except DomainError:
            raise
        except (*_WEBDRIVER_RETRYABLE, TimeoutError, ConnectionError, OSError):
            raise DomainError(
                YUQUE_BROWSER_UNAVAILABLE_CODE,
                "无法打开语雀登录窗口，请确认已安装 Google Chrome",
                503,
                True,
                "检查 Chrome 后重试",
            ) from None

    async def install_browser(self) -> BrowserInstallResult:
        """Ensure a driver matching the installed Chrome is on disk."""
        try:
            from app.yuque.browser import find_browser
            from app.yuque.driver import install_driver

            bundle = await install_driver(find_browser(), self._cache_root)
        except Exception as error:  # noqa: BLE001 - reported to the user verbatim
            return BrowserInstallResult(
                installed=False, message=f"浏览器驱动准备失败：{error}"
            )
        return BrowserInstallResult(
            installed=True, message=f"语雀浏览器驱动已就绪（{bundle.major_version}）"
        )

    async def close(self) -> None:
        return None

    # -- session ---------------------------------------------------------

    @asynccontextmanager
    async def _new_page(self, visible_login: bool):
        async with self._context_lock:
            session = await open_session(
                cache_root=self._cache_root,
                visible=visible_login,
                # A visible login must start clean, otherwise a stale cookie
                # would short-circuit the window the user is meant to use.
                restore_session=not visible_login,
            )
            try:
                yield WdPage(session, render_timeout_ms=3_000)
            finally:
                await session.quit()

    # -- background session ------------------------------------------------

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

    # -- capabilities ----------------------------------------------------
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
            except (*_WEBDRIVER_RETRYABLE, TimeoutError, ConnectionError, OSError):
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
                except (*_WEBDRIVER_RETRYABLE, TimeoutError, ConnectionError, OSError):
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
        except (*_WEBDRIVER_RETRYABLE, TimeoutError, ConnectionError, OSError):
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
        except (*_WEBDRIVER_RETRYABLE, TimeoutError, ConnectionError, OSError):
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
        except (*_WEBDRIVER_RETRYABLE, TimeoutError, ConnectionError, OSError):
            raise DomainError("YUQUE_PAGE_CHANGED", "语雀页面响应异常，请重新登录后重试", 503, True) from None

