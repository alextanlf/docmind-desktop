from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol
from urllib.parse import quote
from uuid import uuid4

from playwright.async_api import BrowserContext, async_playwright
from playwright.async_api import Error as PlaywrightError

from app.api.errors import DomainError
from app.config import AppSettings
from app.schemas.yuque import (
    CreateRepositoryRequest,
    CreateYuqueDocumentRequest,
    LoginResult,
    LoginStatus,
    UpdateYuqueDocumentRequest,
    YuqueDocument,
    YuqueDocumentContent,
    YuqueRepository,
)
from app.yuque.dashboard_page import DashboardPage
from app.yuque.editor_page import EditorPage
from app.yuque.login_page import LoginPage
from app.yuque.repository_page import RepositoryPage


class YuqueGateway(Protocol):
    async def login_status(self) -> LoginStatus: ...

    async def begin_login(self) -> LoginResult: ...

    async def list_repositories(self) -> list[YuqueRepository]: ...

    async def create_repository(self, request: CreateRepositoryRequest) -> YuqueRepository: ...

    async def list_documents(self, repository_id: str) -> list[YuqueDocument]: ...

    async def create_document(self, request: CreateYuqueDocumentRequest) -> YuqueDocument: ...

    async def read_document(self, document_id: str) -> YuqueDocumentContent: ...

    async def update_document(self, request: UpdateYuqueDocumentRequest) -> YuqueDocument: ...

    async def delete_document(self, document_id: str) -> None: ...

    async def close(self) -> None: ...


class FakeYuqueGateway:
    """Deterministic in-memory gateway for API and end-to-end tests."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._repositories: dict[str, YuqueRepository] = {}
        self._documents: dict[str, YuqueDocumentContent] = {}
        self._logged_in = False
        self._active_contexts = 0
        self.max_concurrent_contexts = 0

    async def login_status(self) -> LoginStatus:
        async with self._serialized():
            return LoginStatus(
                logged_in=self._logged_in,
                account_label="f***e" if self._logged_in else None,
                requires_login=not self._logged_in,
            )

    async def begin_login(self) -> LoginResult:
        async with self._serialized():
            self._logged_in = True
            return LoginResult(logged_in=True, account_label="f***e", requires_login=False)

    async def list_repositories(self) -> list[YuqueRepository]:
        async with self._serialized():
            self._require_login(allow_first_use=True)
            return list(self._repositories.values())

    async def create_repository(self, request: CreateRepositoryRequest) -> YuqueRepository:
        async with self._serialized():
            self._require_login(allow_first_use=True)
            repository_id = f"repo-{len(self._repositories) + 1}"
            repository = YuqueRepository(
                yuque_id=repository_id, name=request.name, url=f"https://yuque.local/{repository_id}"
            )
            self._repositories[repository_id] = repository
            return repository

    async def list_documents(self, repository_id: str) -> list[YuqueDocument]:
        async with self._serialized():
            self._require_login(allow_first_use=True)
            self._repository(repository_id)
            return [
                _document_summary(document)
                for document in self._documents.values()
                if document.repository_id == repository_id
            ]

    async def create_document(self, request: CreateYuqueDocumentRequest) -> YuqueDocument:
        async with self._serialized():
            self._require_login(allow_first_use=True)
            self._repository(request.repository_id)
            document_id = f"doc-{len(self._documents) + 1}"
            document = YuqueDocumentContent(
                yuque_id=document_id,
                repository_id=request.repository_id,
                title=request.title,
                content=request.content,
                url=f"https://yuque.local/{document_id}",
            )
            self._documents[document_id] = document
            return _document_summary(document)

    async def read_document(self, document_id: str) -> YuqueDocumentContent:
        async with self._serialized():
            self._require_login(allow_first_use=True)
            return self._document(document_id)

    async def update_document(self, request: UpdateYuqueDocumentRequest) -> YuqueDocument:
        async with self._serialized():
            self._require_login(allow_first_use=True)
            document = self._document(request.document_id).model_copy(
                update={"title": request.title, "content": request.content}
            )
            self._documents[request.document_id] = document
            return _document_summary(document)

    async def delete_document(self, document_id: str) -> None:
        async with self._serialized():
            self._require_login(allow_first_use=True)
            self._document(document_id)
            del self._documents[document_id]

    async def close(self) -> None:
        return None

    def seed_repository(self, yuque_id: str, name: str) -> YuqueRepository:
        repository = YuqueRepository(yuque_id=yuque_id, name=name, url=f"https://yuque.local/{yuque_id}")
        self._repositories[yuque_id] = repository
        return repository

    @asynccontextmanager
    async def _serialized(self) -> AsyncIterator[None]:
        async with self._lock:
            self._active_contexts += 1
            self.max_concurrent_contexts = max(self.max_concurrent_contexts, self._active_contexts)
            try:
                await asyncio.sleep(0)
                yield
            finally:
                self._active_contexts -= 1

    def _require_login(self, allow_first_use: bool = False) -> None:
        if not self._logged_in and not allow_first_use:
            raise DomainError("YUQUE_LOGIN_REQUIRED", "请先登录语雀", 401, False, "重新登录语雀")

    def _repository(self, repository_id: str) -> YuqueRepository:
        try:
            return self._repositories[repository_id]
        except KeyError:
            raise DomainError("YUQUE_PAGE_CHANGED", "语雀知识库不存在或页面已变化", 404) from None

    def _document(self, document_id: str) -> YuqueDocumentContent:
        try:
            return self._documents[document_id]
        except KeyError:
            raise DomainError("YUQUE_PAGE_CHANGED", "语雀文档不存在或页面已变化", 404) from None


class PlaywrightYuqueGateway:
    """Serialized persistent-profile Playwright gateway for real Yuque access."""

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self._context_lock = asyncio.Lock()
        self._playwright: Any | None = None
        self._login_in_progress = False
        self._request_id = uuid4().hex

    async def login_status(self) -> LoginStatus:
        async with self._new_page(visible_login=False) as page:
            await page.goto("https://www.yuque.com/dashboard", wait_until="domcontentloaded")
            login = LoginPage(page, self.settings.screenshots_dir, self._request_id)
            logged_in = await login.is_logged_in()
            label = await login.account_label() if logged_in else None
            return LoginStatus(
                logged_in=logged_in,
                account_label=_mask_account(label),
                requires_login=not logged_in,
            )

    async def begin_login(self) -> LoginResult:
        if self._login_in_progress or self._context_lock.locked():
            raise DomainError("YUQUE_LOGIN_IN_PROGRESS", "语雀登录正在进行", 409, False)
        self._login_in_progress = True
        try:
            async with self._new_page(visible_login=True) as page:
                await page.goto("https://www.yuque.com/login", wait_until="domcontentloaded")
                login = LoginPage(page, self.settings.screenshots_dir, self._request_id)
                if not await login.wait_until_logged_in(timeout=600_000):
                    await login._capture_failure("login-timeout")
                    raise DomainError("YUQUE_LOGIN_REQUIRED", "语雀登录超时或已取消", 408, True, "重新登录语雀")
                return LoginResult(
                    logged_in=True,
                    account_label=_mask_account(await login.account_label()),
                    requires_login=False,
                )
        except DomainError:
            raise
        except PlaywrightError:
            raise DomainError("YUQUE_LOGIN_REQUIRED", "语雀登录状态不可用", 401, True, "重新登录语雀") from None
        finally:
            self._login_in_progress = False

    async def list_repositories(self) -> list[YuqueRepository]:
        async with self._background_page("list-repositories") as page:
            return await DashboardPage(page, self.settings.screenshots_dir, self._request_id).with_retry(
                "list-repositories", DashboardPage(page).list_repositories
            )

    async def create_repository(self, request: CreateRepositoryRequest) -> YuqueRepository:
        async with self._background_page("create-repository") as page:
            dashboard = DashboardPage(page, self.settings.screenshots_dir, self._request_id)
            return await dashboard.with_retry("create-repository", lambda: dashboard.create_repository(request.name))

    async def list_documents(self, repository_id: str) -> list[YuqueDocument]:
        async with self._background_page("list-documents") as page:
            await _open_yuque_resource(page, repository_id)
            repository = RepositoryPage(page, self.settings.screenshots_dir, self._request_id)
            return await repository.with_retry(
                "list-documents", lambda: repository.list_documents(repository_id)
            )

    async def create_document(self, request: CreateYuqueDocumentRequest) -> YuqueDocument:
        async with self._background_page("create-document") as page:
            await _open_yuque_resource(page, request.repository_id)
            repository = RepositoryPage(page, self.settings.screenshots_dir, self._request_id)
            editor = EditorPage(page, self.settings.screenshots_dir, self._request_id)

            async def create() -> YuqueDocument:
                await repository.open_new_document()
                await editor.set_title(request.title)
                await editor.import_markdown(request.content)
                documents = await repository.list_documents(request.repository_id)
                return next(document for document in documents if document.title == request.title)

            return await editor.with_retry("create-document", create)

    async def read_document(self, document_id: str) -> YuqueDocumentContent:
        async with self._background_page("read-document") as page:
            await _open_yuque_resource(page, document_id)
            editor = EditorPage(page, self.settings.screenshots_dir, self._request_id)
            content = await editor.with_retry("read-document", editor.read_markdown)
            return YuqueDocumentContent(
                yuque_id=document_id,
                repository_id="",
                title="",
                content=content,
                url=page.url,
            )

    async def update_document(self, request: UpdateYuqueDocumentRequest) -> YuqueDocument:
        async with self._background_page("update-document") as page:
            await _open_yuque_resource(page, request.document_id)
            editor = EditorPage(page, self.settings.screenshots_dir, self._request_id)

            async def update() -> YuqueDocument:
                await editor.set_title(request.title)
                await editor.import_markdown(request.content)
                return YuqueDocument(
                    yuque_id=request.document_id, repository_id="", title=request.title, url=page.url
                )

            return await editor.with_retry("update-document", update)

    async def delete_document(self, document_id: str) -> None:
        async with self._background_page("delete-document") as page:
            await _open_yuque_resource(page, document_id)
            repository = RepositoryPage(page, self.settings.screenshots_dir, self._request_id)
            await repository.with_retry("delete-document", repository.delete_current_document)

    async def close(self) -> None:
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    @asynccontextmanager
    async def _background_page(self, operation: str) -> AsyncIterator[Any]:
        async with self._new_page(visible_login=False) as page:
            await page.goto("https://www.yuque.com/dashboard", wait_until="domcontentloaded")
            login = LoginPage(page, self.settings.screenshots_dir, self._request_id)
            if not await login.is_logged_in():
                raise DomainError("YUQUE_LOGIN_REQUIRED", "语雀登录已失效，请重新登录", 401, False, "重新登录语雀")
            yield page

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
                yield page
            finally:
                if context is not None:
                    await context.close()


def _mask_account(value: str | None) -> str | None:
    if not value:
        return None
    compact = value.strip()
    if len(compact) <= 2:
        return "*" * len(compact)
    return f"{compact[0]}***{compact[-1]}"


def _document_summary(document: YuqueDocumentContent) -> YuqueDocument:
    return YuqueDocument.model_validate(document.model_dump(exclude={"content"}))


async def _open_yuque_resource(page: Any, resource_id: str) -> None:
    url = resource_id if resource_id.startswith("https://www.yuque.com/") else (
        f"https://www.yuque.com/{quote(resource_id.strip('/'), safe='/')}"
    )
    await page.goto(url, wait_until="domcontentloaded")
