from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import httpx

from app.api.errors import DomainError
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
from app.yuque.codes import YUQUE_BROWSER_UNAVAILABLE_CODE

YUQUE_API_BASE_URL = "https://www.yuque.com/api/v2"
_PAGE_LIMIT = 100
_MAX_ITEMS = 1_000


class YuqueApiGateway:
    """Yuque Open API gateway using a personal access token."""

    def __init__(self, token_provider: Callable[[], str | None]) -> None:
        self._token_provider = token_provider
        self._context_lock = asyncio.Lock()
        self.read_calls: list[str] = []
        self.write_calls: list[str] = []

    async def login_status(self) -> LoginStatus:
        token = self._token()
        if token is None:
            return LoginStatus(logged_in=False, account_label=None, requires_login=True)
        try:
            user = await _fetch_user(token)
        except DomainError as error:
            if error.code in {"YUQUE_API_AUTH_FAILED", "YUQUE_API_UNAVAILABLE"}:
                return LoginStatus(logged_in=False, account_label=None, requires_login=True)
            raise
        return LoginStatus(
            logged_in=True,
            account_label=mask_account(str(user.get("name") or user.get("login") or "")),
            requires_login=False,
        )

    async def begin_login(self) -> LoginResult:
        token = self._token()
        if token:
            user = await _fetch_user(token)
            return LoginResult(
                logged_in=True,
                account_label=mask_account(str(user.get("name") or user.get("login") or "")),
                requires_login=False,
            )
        raise DomainError(
            "YUQUE_API_TOKEN_REQUIRED",
            "请先配置语雀 API Token",
            400,
            False,
            "在设置中填写语雀 API Token",
        )

    async def install_browser(self) -> BrowserInstallResult:
        return BrowserInstallResult(installed=True, message="API 模式无需安装浏览器")

    async def list_repositories(self) -> list[RemoteRepository]:
        self.read_calls.append("list_repositories")
        token = self._token_required()
        user = await _fetch_user(token)
        login = str(user.get("login") or "")
        if not login:
            raise DomainError("YUQUE_API_ERROR", "语雀 API 未返回用户标识", 502, True)
        rows = await _list_all(token, f"/users/{login}/repos")
        return [_repository_from_api(row) for row in rows]

    async def create_repository(self, request: CreateRemoteRepositoryRequest) -> RemoteRepository:
        self.write_calls.append("create_repository")
        token = self._token_required()
        user = await _fetch_user(token)
        login = str(user.get("login") or "")
        if not login:
            raise DomainError("YUQUE_API_ERROR", "语雀 API 未返回用户标识", 502, True)
        data = await _request_data(
            token,
            "POST",
            f"/users/{login}/repos",
            json={"name": request.name, "public": 0},
        )
        if not isinstance(data, dict):
            raise DomainError("YUQUE_API_ERROR", "语雀 API 返回的知识库数据无效", 502, True)
        return _repository_from_api(data)

    async def list_documents(self, repository_id: str) -> list[RemoteDocument]:
        self.read_calls.append("list_documents")
        token = self._token_required()
        rows = await _list_all(token, f"/repos/{_quote_path(repository_id)}/docs")
        return [_document_from_api(repository_id, row) for row in rows]

    async def create_document(self, request: CreateRemoteDocumentRequest) -> RemoteDocument:
        self.write_calls.append("create_document")
        token = self._token_required()
        data = await _request_data(
            token,
            "POST",
            f"/repos/{_quote_path(request.repository_id)}/docs",
            json={"title": request.title, "body": request.content, "public": 0},
        )
        if not isinstance(data, dict):
            raise DomainError("YUQUE_API_ERROR", "语雀 API 返回的文档数据无效", 502, True)
        return _document_from_api(request.repository_id, data)

    async def find_document_by_marker(
        self, repository_id: str, marker: str
    ) -> RemoteDocument | None:
        for document in await self.list_documents(repository_id):
            content = await self.read_document(document.remote_id)
            if marker in content.content:
                return document
        return None

    async def document_exists(self, repository_id: str, document_id: str) -> bool:
        try:
            await self._fetch_document(document_id)
        except DomainError as error:
            if error.code == "YUQUE_API_NOT_FOUND":
                return False
            raise
        return _document_repository_id(document_id) == repository_id.strip("/")

    async def read_document(self, document_id: str) -> RemoteDocumentContent:
        self.read_calls.append("read_document")
        token = self._token_required()
        repository_id, slug = _parse_document_id(document_id)
        data = await _request_data(
            token, "GET", f"/repos/{_quote_path(repository_id)}/docs/{_quote_path(slug)}"
        )
        if not isinstance(data, dict):
            raise DomainError("YUQUE_API_ERROR", "语雀 API 返回的文档数据无效", 502, True)
        title = str(data.get("title") or slug)
        url = _document_url(data, repository_id, slug)
        return RemoteDocumentContent(
            remote_id=document_id,
            repository_id=repository_id,
            title=title,
            content=_document_content(data, title, url),
            url=url,
        )

    async def update_document(self, request: UpdateRemoteDocumentRequest) -> RemoteDocument:
        self.write_calls.append("update_document")
        token = self._token_required()
        repository_id, _ = _parse_document_id(request.document_id)
        current = await self._fetch_document(request.document_id)
        api_id = current.get("id")
        if api_id is None:
            raise DomainError("YUQUE_API_ERROR", "语雀 API 未返回文档标识", 502, True)
        data = await _request_data(
            token,
            "PUT",
            f"/repos/{_quote_path(repository_id)}/docs/{api_id}",
            json={"title": request.title, "body": request.content},
        )
        if not isinstance(data, dict):
            raise DomainError("YUQUE_API_ERROR", "语雀 API 返回的文档数据无效", 502, True)
        return _document_from_api(repository_id, data, fallback_id=request.document_id)

    async def delete_document(self, document_id: str, repository_id: str) -> None:
        self.write_calls.append("delete_document")
        token = self._token_required()
        current = await self._fetch_document(document_id)
        api_id = current.get("id")
        if api_id is None:
            raise DomainError("YUQUE_API_ERROR", "语雀 API 未返回文档标识", 502, True)
        await _request_data(
            token, "DELETE", f"/repos/{_quote_path(repository_id)}/docs/{api_id}"
        )

    async def close(self) -> None:
        return None

    async def _fetch_document(self, document_id: str) -> dict[str, Any]:
        token = self._token_required()
        repository_id, slug = _parse_document_id(document_id)
        data = await _request_data(
            token, "GET", f"/repos/{_quote_path(repository_id)}/docs/{_quote_path(slug)}"
        )
        if not isinstance(data, dict):
            raise DomainError("YUQUE_API_ERROR", "语雀 API 返回的文档数据无效", 502, True)
        return data

    def _token(self) -> str | None:
        value = self._token_provider()
        return value.strip() if value and value.strip() else None

    def _token_required(self) -> str:
        token = self._token()
        if token is None:
            raise DomainError(
                "YUQUE_LOGIN_REQUIRED",
                "尚未绑定语雀 API",
                401,
                False,
                "配置语雀 API Token",
                auth_expired=True,
            )
        return token


class YuqueProvider:
    """The yuque remote provider: API token first, browser session fallback.

    Document operations route to the verified open API when a token is
    available and fall back to the Playwright-driven web session. Login
    surface (status/login/browser install) always reflects the web session,
    matching the former /api/yuque behaviour, because the account binding
    card tracks the browser login state.
    """

    identity = ProviderIdentity(
        name="yuque",
        label="语雀",
        capabilities=ProviderCapabilities(
            browser_install=True,
            marker_lookup=True,
            browser_unavailable_code=YUQUE_BROWSER_UNAVAILABLE_CODE,
        ),
    )

    def __init__(
        self,
        web_gateway: Any,
        api_gateway: YuqueApiGateway,
        api_available: Callable[[], bool],
    ) -> None:
        self.web_gateway = web_gateway
        self.api_gateway = api_gateway
        self._api_available = api_available
        self._context_lock = asyncio.Lock()

    def _active(self) -> Any:
        return self.api_gateway if self._api_available() else self.web_gateway

    def __getattr__(self, name: str) -> Any:
        # Preserve the diagnostics and test seams exposed by the concrete web
        # gateway while still routing production operations through the API.
        return getattr(self._active(), name)

    async def login_status(self) -> LoginStatus:
        return await self.web_gateway.login_status()

    async def begin_login(self) -> LoginResult:
        return await self.web_gateway.begin_login()

    async def install_browser(self) -> BrowserInstallResult:
        return await self.web_gateway.install_browser()

    async def list_repositories(self) -> list[RemoteRepository]:
        return await self._active().list_repositories()

    async def create_repository(self, request: CreateRemoteRepositoryRequest) -> RemoteRepository:
        return await self._active().create_repository(request)

    async def list_documents(self, repository_id: str) -> list[RemoteDocument]:
        return await self._active().list_documents(repository_id)

    async def create_document(self, request: CreateRemoteDocumentRequest) -> RemoteDocument:
        return await self._active().create_document(request)

    async def find_document_by_marker(
        self, repository_id: str, marker: str
    ) -> RemoteDocument | None:
        return await self._active().find_document_by_marker(repository_id, marker)

    async def document_exists(self, repository_id: str, document_id: str) -> bool:
        return await self._active().document_exists(repository_id, document_id)

    async def read_document(self, document_id: str) -> RemoteDocumentContent:
        return await self._active().read_document(document_id)

    async def update_document(self, request: UpdateRemoteDocumentRequest) -> RemoteDocument:
        return await self._active().update_document(request)

    async def delete_document(self, document_id: str, repository_id: str) -> None:
        await self._active().delete_document(document_id, repository_id)

    async def close(self) -> None:
        await asyncio.gather(
            self.web_gateway.close(),
            self.api_gateway.close(),
            return_exceptions=True,
        )


async def _fetch_user(token: str) -> dict[str, Any]:
    data = await _request_data(token, "GET", "/user")
    if not isinstance(data, dict):
        raise DomainError("YUQUE_API_ERROR", "语雀 API 返回的用户数据无效", 502, True)
    return data


async def _list_all(token: str, path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while len(rows) < _MAX_ITEMS:
        data = await _request_data(
            token,
            "GET",
            path,
            params={"limit": _PAGE_LIMIT, "offset": offset},
        )
        if not isinstance(data, list):
            raise DomainError("YUQUE_API_ERROR", "语雀 API 返回的列表数据无效", 502, True)
        page = [row for row in data if isinstance(row, dict)]
        rows.extend(page)
        if len(data) < _PAGE_LIMIT or not page:
            break
        offset += len(data)
    return rows[:_MAX_ITEMS]


async def _request_data(
    token: str,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
) -> Any:
    try:
        async with httpx.AsyncClient(
            base_url=YUQUE_API_BASE_URL,
            headers={"X-Auth-Token": token, "Accept": "application/json"},
            timeout=httpx.Timeout(15.0),
            follow_redirects=False,
        ) as client:
            response = await client.request(method, path, params=params, json=json)
    except httpx.HTTPError as error:
        raise DomainError(
            "YUQUE_API_UNAVAILABLE", "无法连接语雀 API，请检查网络后重试", 503, True
        ) from error

    if response.status_code in {401, 403}:
        raise DomainError("YUQUE_API_AUTH_FAILED", "语雀 API Token 无效或已过期", 401, False)
    if response.status_code == 404:
        raise DomainError("YUQUE_API_NOT_FOUND", "语雀资源不存在", 404, False)
    if response.status_code >= 400:
        raise DomainError("YUQUE_API_ERROR", "语雀 API 请求失败", 502, True)
    if response.status_code == 204:
        return None
    try:
        payload = response.json()
    except ValueError as error:
        raise DomainError("YUQUE_API_ERROR", "语雀 API 返回的数据格式无效", 502, True) from error
    if not isinstance(payload, dict):
        raise DomainError("YUQUE_API_ERROR", "语雀 API 返回的数据格式无效", 502, True)
    return payload.get("data")


def _repository_from_api(row: dict[str, Any]) -> RemoteRepository:
    namespace = str(row.get("namespace") or "").strip("/")
    slug = str(row.get("slug") or "").strip("/")
    if not namespace or not slug:
        raise DomainError("YUQUE_API_ERROR", "语雀 API 返回的知识库标识无效", 502, True)
    repository_id = f"{namespace}/{slug}"
    return RemoteRepository(
        remote_id=repository_id,
        name=str(row.get("name") or slug),
        url=f"https://www.yuque.com/{repository_id}",
    )


def _document_from_api(
    repository_id: str, row: dict[str, Any], fallback_id: str | None = None
) -> RemoteDocument:
    slug = str(row.get("slug") or "").strip("/")
    if not slug:
        if fallback_id:
            return RemoteDocument(
                remote_id=fallback_id,
                repository_id=repository_id,
                title=str(row.get("title") or fallback_id),
                url=f"https://www.yuque.com/{fallback_id}",
            )
        raise DomainError("YUQUE_API_ERROR", "语雀 API 返回的文档标识无效", 502, True)
    document_id = f"{repository_id.strip('/')}/{slug}"
    return RemoteDocument(
        remote_id=document_id,
        repository_id=repository_id,
        title=str(row.get("title") or slug),
        url=_document_url(row, repository_id, slug),
    )


def _document_url(row: dict[str, Any], repository_id: str, slug: str) -> str:
    value = row.get("url")
    if isinstance(value, str) and value.startswith("https://www.yuque.com/"):
        return value
    return f"https://www.yuque.com/{repository_id.strip('/')}/{slug}"


def _document_content(row: dict[str, Any], title: str, url: str) -> str:
    body = row.get("body")
    if isinstance(body, str) and body.strip():
        return body
    body_html = row.get("body_html")
    if isinstance(body_html, str) and body_html.strip():
        parsed = DocumentParser().parse(
            DownloadedDocument(
                title=title,
                source_url=url,
                media_type="text/html",
                raw_bytes=body_html.encode("utf-8"),
            )
        )
        return parsed.markdown
    return ""


def _parse_document_id(document_id: str) -> tuple[str, str]:
    value = document_id.strip()
    parsed = urlparse(value)
    if parsed.scheme and parsed.hostname == "www.yuque.com":
        value = parsed.path
    parts = [part for part in value.strip("/").split("/") if part]
    if len(parts) < 2:
        raise DomainError("YUQUE_API_ERROR", "语雀文档标识无效", 400, False)
    return "/".join(parts[:-1]), parts[-1]


def _document_repository_id(document_id: str) -> str:
    repository_id, _ = _parse_document_id(document_id)
    return repository_id


def _quote_path(value: str) -> str:
    # Path segments are kept readable for Yuque namespaces while unsafe characters
    # are escaped by httpx when the URL is dispatched.
    return value.strip("/")
