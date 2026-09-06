from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.api.errors import DomainError
from app.schemas.web_search import SearchRequest
from app.storage.repositories import WebSearchRunStore


@dataclass
class SearchRunView:
    id: UUID
    status: str
    results: list

class SearchService:
    def __init__(self, provider, run_store: WebSearchRunStore, secret_store=None):
        self.provider, self.run_store, self.secret_store = provider, run_store, secret_store

    async def run(self, request, *, authorization_mode: str = "auto") -> SearchRunView:
        if authorization_mode not in ("auto", "explicit"):
            raise DomainError("SEARCH_PERMISSION_DENIED", "搜索未获授权", 403)
        try:
            configured = self.secret_store is not None and bool(
                self.secret_store.get("web-search:tavily")
            )
        except (DomainError, OSError):
            # Keychain failures must fail closed and never invoke the provider.
            configured = False
        if not configured:
            raise DomainError("SEARCH_AUTH_FAILED", "请先配置搜索 API Key", 400)
        existing = self.run_store.by_request_id(str(request.request_id))
        if existing is not None and (
            existing.session_id != str(request.session_id)
            or existing.user_message_id != str(request.user_message_id)
        ):
            raise DomainError("SEARCH_REQUEST_CONFLICT", "请求标识已被其他会话使用", 409)
        row = self.run_store.create(request_id=str(request.request_id), session_id=str(request.session_id), user_message_id=str(request.user_message_id), query=request.query)
        if existing is not None and row.status == "completed":
            return SearchRunView(id=UUID(row.id), status=row.status, results=self.run_store.results(row.id))
        if existing is not None and row.status == "running":
            # A concurrent or retried request reuses the durable run; do not invoke provider twice.
            return SearchRunView(id=UUID(row.id), status=row.status, results=self.run_store.results(row.id))
        try:
            response = await self.provider.search(SearchRequest(query=request.query, max_results=getattr(request, 'max_results', 5)))
            self.run_store.complete(row.id, response.results)
            return SearchRunView(id=UUID(row.id), status="completed", results=self.run_store.results(row.id))
        except Exception as exc:
            self.run_store.fail(row.id, error_code="SEARCH_PROVIDER_ERROR")
            raise DomainError("SEARCH_PROVIDER_ERROR", "搜索服务暂时不可用", 502, True) from exc
