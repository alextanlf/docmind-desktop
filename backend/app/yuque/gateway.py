from __future__ import annotations

import asyncio
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol
from urllib.parse import quote, urlparse
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
from app.yuque.base_page import RETRY_DELAYS
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

    async def find_document_by_marker(
        self, repository_id: str, marker: str
    ) -> YuqueDocument | None: ...

    async def document_exists(self, repository_id: str, document_id: str) -> bool: ...

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
        self.read_calls: list[str] = []
        self.write_calls: list[str] = []

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
        self.write_calls.append("create_repository")
        async with self._serialized():
            self._require_login(allow_first_use=True)
            repository_id = f"repo-{len(self._repositories) + 1}"
            repository = YuqueRepository(
                yuque_id=repository_id, name=request.name, url=f"https://yuque.local/{repository_id}"
            )
            self._repositories[repository_id] = repository
            return repository

    async def list_documents(self, repository_id: str) -> list[YuqueDocument]:
        self.read_calls.append("list_documents")
        async with self._serialized():
            self._require_login(allow_first_use=True)
            self._repository(repository_id)
            return [
                _document_summary(document)
                for document in self._documents.values()
                if document.repository_id == repository_id
            ]

    async def create_document(self, request: CreateYuqueDocumentRequest) -> YuqueDocument:
        self.write_calls.append("create_document")
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

    async def find_document_by_marker(
        self, repository_id: str, marker: str
    ) -> YuqueDocument | None:
        async with self._serialized():
            self._require_login(allow_first_use=True)
            self._repository(repository_id)
            return next(
                (
                    _document_summary(document)
                    for document in self._documents.values()
                    if document.repository_id == repository_id and marker in document.content
                ),
                None,
            )

    async def document_exists(self, repository_id: str, document_id: str) -> bool:
        async with self._serialized():
            self._require_login(allow_first_use=True)
            self._repository(repository_id)
            document = self._documents.get(document_id)
            return document is not None and document.repository_id == repository_id

    async def read_document(self, document_id: str) -> YuqueDocumentContent:
        self.read_calls.append("read_document")
        async with self._serialized():
            self._require_login(allow_first_use=True)
            document = self._document(document_id)
            return document.model_copy(update={"content": _strip_mutation_marker(document.content)})

    async def update_document(self, request: UpdateYuqueDocumentRequest) -> YuqueDocument:
        self.write_calls.append("update_document")
        async with self._serialized():
            self._require_login(allow_first_use=True)
            document = self._document(request.document_id).model_copy(
                update={"title": request.title, "content": request.content}
            )
            self._documents[request.document_id] = document
            return _document_summary(document)

    async def delete_document(self, document_id: str) -> None:
        self.write_calls.append("delete_document")
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
        self.read_calls: list[str] = []
        self.write_calls: list[str] = []
        self._context_lock = asyncio.Lock()
        self._login_lock = asyncio.Lock()
        self._playwright: Any | None = None

    async def login_status(self) -> LoginStatus:
        request_id = uuid4().hex
        async with self._new_page(visible_login=False) as page:
            login = LoginPage(page, self.settings.screenshots_dir, request_id)

            async def status() -> LoginStatus:
                await page.goto("https://www.yuque.com/dashboard", wait_until="domcontentloaded")
                if await login.is_logged_in():
                    return LoginStatus(
                        logged_in=True,
                        account_label=_mask_account(await login.account_label()),
                        requires_login=False,
                    )
                if _is_login_url(page.url):
                    return LoginStatus(logged_in=False, account_label=None, requires_login=True)
                raise DomainError("YUQUE_PAGE_CHANGED", "语雀页面结构已变化，请重新登录后重试", 503, True)

            return await login.with_retry("login-status", status)

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

    async def list_repositories(self) -> list[YuqueRepository]:
        async with self._background_page("list-repositories") as operation:
            page, request_id = operation
            return await DashboardPage(page, self.settings.screenshots_dir, request_id).with_retry(
                "list-repositories", DashboardPage(page).list_repositories
            )

    async def create_repository(self, request: CreateRepositoryRequest) -> YuqueRepository:
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

    async def list_documents(self, repository_id: str) -> list[YuqueDocument]:
        async with self._background_page("list-documents") as operation:
            page, request_id = operation
            repository = RepositoryPage(page, self.settings.screenshots_dir, request_id)

            async def list_documents() -> list[YuqueDocument]:
                await _open_yuque_resource(page, repository_id)
                return await repository.list_documents(repository_id)

            return await repository.with_retry("list-documents", list_documents)

    async def create_document(self, request: CreateYuqueDocumentRequest) -> YuqueDocument:
        self.write_calls.append("create_document")
        async with self._background_page("create-document") as operation:
            page, request_id = operation
            repository = RepositoryPage(page, self.settings.screenshots_dir, request_id)
            editor = EditorPage(page, self.settings.screenshots_dir, request_id)

            async def create() -> None:
                await _open_yuque_resource(page, request.repository_id)
                await repository.open_new_document()
                await editor.set_title(request.title)
                await editor.import_markdown(request.content)

            async def confirm_created_document() -> YuqueDocument:
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
                return YuqueDocument(
                    yuque_id=document_id,
                    repository_id=request.repository_id,
                    title=title,
                    url=page.url,
                )

            try:
                await create()
            except DomainError as error:
                if not error.retryable:
                    raise
                await editor._capture_failure("create-document")
                raise DomainError(
                    "YUQUE_PAGE_CHANGED", "语雀页面响应异常，请重新登录后重试", 503, True
                ) from None
            except (PlaywrightError, TimeoutError, ConnectionError, OSError):
                await editor._capture_failure("create-document")
                raise DomainError(
                    "YUQUE_PAGE_CHANGED", "语雀页面响应异常，请重新登录后重试", 503, True
                ) from None
            return await editor.with_retry("confirm-created-document", confirm_created_document)

    async def find_document_by_marker(
        self, repository_id: str, marker: str
    ) -> YuqueDocument | None:
        for delay in (*RETRY_DELAYS, None):
            documents = await self.list_documents(repository_id)
            for document in documents:
                content = await self._read_document(document.yuque_id, strip_mutation_marker=False)
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
            target in {_resource_identity(document.yuque_id), _resource_identity(document.url)}
            for document in documents
        )

    async def read_document(self, document_id: str) -> YuqueDocumentContent:
        self.read_calls.append("read_document")
        return await self._read_document(document_id, strip_mutation_marker=True)

    async def _read_document(
        self, document_id: str, *, strip_mutation_marker: bool
    ) -> YuqueDocumentContent:
        async with self._background_page("read-document") as operation:
            page, request_id = operation
            editor = EditorPage(page, self.settings.screenshots_dir, request_id)

            async def read() -> YuqueDocumentContent:
                await _open_yuque_resource(page, document_id)
                content = await editor.read_markdown()
                if strip_mutation_marker:
                    content = _strip_mutation_marker(content)
                return YuqueDocumentContent(
                    yuque_id=document_id,
                    repository_id=_repository_id_from_document_url(page.url),
                    title=await editor.read_title(),
                    content=content,
                    url=page.url,
                )

            return await editor.with_retry("read-document", read)

    async def update_document(self, request: UpdateYuqueDocumentRequest) -> YuqueDocument:
        self.write_calls.append("update_document")
        async with self._background_page("update-document") as operation:
            page, request_id = operation
            editor = EditorPage(page, self.settings.screenshots_dir, request_id)

            async def update() -> YuqueDocument:
                await _open_yuque_resource(page, request.document_id)
                await editor.set_title(request.title)
                await editor.import_markdown(request.content)
                return YuqueDocument(
                    yuque_id=request.document_id,
                    repository_id=_repository_id_from_document_url(page.url),
                    title=await editor.read_title(),
                    url=page.url,
                )

            return await editor.with_retry("update-document", update)

    async def delete_document(self, document_id: str) -> None:
        self.write_calls.append("delete_document")
        async with self._background_page("delete-document") as operation:
            page, request_id = operation
            repository = RepositoryPage(page, self.settings.screenshots_dir, request_id)

            async def delete() -> None:
                await _open_yuque_resource(page, document_id)
                await repository.delete_current_document()

            await repository.with_retry("delete-document", delete)

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
                await page.goto("https://www.yuque.com/dashboard", wait_until="domcontentloaded")
                if await login.is_logged_in():
                    return
                if _is_login_url(page.url):
                    raise DomainError("YUQUE_LOGIN_REQUIRED", "语雀登录已失效，请重新登录", 401, False, "重新登录语雀")
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


_MUTATION_MARKER_RE = re.compile(r"\n\n<!--\s*docmind-mutation:[^>]+-->\Z")


def _strip_mutation_marker(content: str) -> str:
    return _MUTATION_MARKER_RE.sub("", content)
