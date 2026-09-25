from __future__ import annotations

import httpx

from app.schemas.web_search import (
    NormalizedSearchResult,
    SearchConnectionResult,
    SearchRequest,
    SearchResponse,
)
from app.search.provider import SearchProviderError

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class SearxngProvider:
    """Query a user-configured SearXNG instance through its JSON API."""

    name = "searxng"

    def __init__(
        self, base_url: str, client: httpx.AsyncClient | None = None, *, timeout_seconds: float = 20
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client
        self.timeout_seconds = timeout_seconds

    def available(self) -> bool:
        return bool(self.base_url)

    async def search(self, request: SearchRequest) -> SearchResponse:
        own = self.client is None
        client = self.client or httpx.AsyncClient(
            timeout=self.timeout_seconds, follow_redirects=True
        )
        try:
            response = await client.get(
                f"{self.base_url}/search",
                params={
                    "q": request.query,
                    "format": "json",
                    "language": "zh-CN",
                    "safesearch": "1",
                },
                headers={"Accept": "application/json", "User-Agent": _USER_AGENT},
            )
        except httpx.TimeoutException as error:
            raise SearchProviderError("SEARCH_PROVIDER_TIMEOUT", "SearXNG 实例响应超时") from error
        except httpx.HTTPError as error:
            raise SearchProviderError("SEARCH_PROVIDER_ERROR", "无法连接 SearXNG 实例") from error
        finally:
            if own:
                await client.aclose()
        if response.status_code in (401, 403):
            raise SearchProviderError(
                "SEARCH_PROVIDER_ERROR",
                "SearXNG 实例未开放 JSON 输出，请在 settings.yml 的 search.formats 中加入 json",
                retryable=False,
            )
        if response.status_code >= 400:
            raise SearchProviderError("SEARCH_PROVIDER_ERROR", "SearXNG 实例暂时不可用")
        try:
            data = response.json()
        except ValueError as error:
            raise SearchProviderError(
                "SEARCH_PROVIDER_ERROR",
                "SearXNG 返回的不是 JSON，请确认实例地址与 JSON 输出配置",
                retryable=False,
            ) from error
        if not isinstance(data, dict):
            raise SearchProviderError(
                "SEARCH_PROVIDER_ERROR",
                "SearXNG 返回的不是 JSON，请确认实例地址与 JSON 输出配置",
                retryable=False,
            )
        return _parse_results(data, request.max_results)

    async def test_connection(self) -> SearchConnectionResult:
        try:
            await self.search(SearchRequest(query="DocMind", max_results=1))
        except SearchProviderError as error:
            return SearchConnectionResult(ok=False, provider=self.name, message=error.message)
        except Exception:  # noqa: BLE001 - connection test reports failure instead of raising
            return SearchConnectionResult(
                ok=False, provider=self.name, message="SearXNG 实例连接失败"
            )
        return SearchConnectionResult(ok=True, provider=self.name, message="SearXNG 可用")


def _parse_results(data: dict, max_results: int) -> SearchResponse:
    items = data.get("results")
    results: list[NormalizedSearchResult] = []
    for item in items if isinstance(items, list) else []:
        if len(results) >= max_results or not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        title = item.get("title")
        content = item.get("content")
        snippet = content if isinstance(content, str) else ""
        try:
            results.append(
                NormalizedSearchResult(
                    rank=len(results) + 1,
                    canonical_url=url,
                    title=(title if isinstance(title, str) and title.strip() else url)[:512],
                    snippet=snippet[:10_000],
                    content=snippet[: 50 * 1024],
                )
            )
        except ValueError:
            continue
    if not results:
        raise SearchProviderError("SEARCH_PROVIDER_ERROR", "SearXNG 没有返回结果")
    return SearchResponse(results=results, provider="searxng")
