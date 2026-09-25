from __future__ import annotations

import httpx

from app.schemas.web_search import (
    NormalizedSearchResult,
    SearchConnectionResult,
    SearchRequest,
    SearchResponse,
)
from app.search.provider import SearchProviderError


class TavilyProvider:
    name = "tavily"

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None):
        self.api_key = api_key
        self.client = client

    def available(self) -> bool:
        return bool(self.api_key)

    async def search(self, request: SearchRequest) -> SearchResponse:
        own = self.client is None
        client = self.client or httpx.AsyncClient(base_url="https://api.tavily.com", timeout=20)
        try:
            response = await client.post(
                "/search",
                json={
                    "api_key": self.api_key,
                    "query": request.query,
                    "max_results": request.max_results,
                    "include_content": True,
                },
            )
        except httpx.TimeoutException as error:
            raise SearchProviderError("SEARCH_PROVIDER_TIMEOUT", "Tavily 响应超时") from error
        except httpx.HTTPError as error:
            raise SearchProviderError("SEARCH_PROVIDER_ERROR", "Tavily 请求失败") from error
        finally:
            if own:
                await client.aclose()
        if response.status_code in (401, 403):
            raise SearchProviderError("SEARCH_AUTH_FAILED", "Tavily API Key 无效", retryable=False)
        if response.status_code == 429:
            raise SearchProviderError("SEARCH_PROVIDER_RATE_LIMITED", "Tavily 请求过于频繁")
        if response.status_code >= 400:
            raise SearchProviderError("SEARCH_PROVIDER_ERROR", "Tavily 暂时不可用")
        try:
            data = response.json()
        except ValueError as error:
            raise SearchProviderError(
                "SEARCH_PROVIDER_PROTOCOL_ERROR", "Tavily 返回了无法识别的数据", retryable=False
            ) from error
        results: list[NormalizedSearchResult] = []
        items = data.get("results") if isinstance(data, dict) else None
        for position, item in enumerate(items if isinstance(items, list) else [], start=1):
            if len(results) >= request.max_results or not isinstance(item, dict):
                continue
            try:
                results.append(
                    NormalizedSearchResult(
                        rank=len(results) + 1,
                        canonical_url=item.get("url"),
                        title=(item.get("title") or "")[:512],
                        snippet=(item.get("snippet") or "")[:10_000],
                        content=(item.get("content") or item.get("snippet") or "")[: 50 * 1024],
                    )
                )
            except ValueError:
                continue
        if not results:
            raise SearchProviderError("SEARCH_PROVIDER_ERROR", "Tavily 没有返回结果")
        return SearchResponse(results=results, provider=self.name)

    async def test_connection(self) -> SearchConnectionResult:
        try:
            await self.search(SearchRequest(query="test", max_results=1))
        except Exception:  # noqa: BLE001 - connection test reports failure instead of raising
            return SearchConnectionResult(
                ok=False, provider=self.name, message="Tavily 连接失败"
            )
        return SearchConnectionResult(ok=True, provider=self.name, message="Tavily 可用")
