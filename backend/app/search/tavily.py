from __future__ import annotations

import httpx

from app.schemas.web_search import (
    NormalizedSearchResult,
    SearchConnectionResult,
    SearchRequest,
    SearchResponse,
)
from app.search.provider import SearchProviderError

# 历史 Key 的存储名。设置页的「联网搜索」分区已删除，这里保留读取是为了让
# 以前配过 Key 的安装继续用上自己的额度（付费档不限流），只是不再有输入口。
TAVILY_SECRET_NAME = "web-search:tavily"


class TavilyProvider:
    """Tavily 搜索。

    免密钥模式是默认形态：官方支持 `X-Tavily-Access-Mode: keyless`，无需注册即可调用，
    所以 Tavily 现在是**零配置**来源 —— 这也是设置页那一栏能被删掉的前提。
    配过 Key 的安装仍走带 Key 的路径。
    """

    name = "tavily"

    def __init__(self, api_key: str | None = None, client: httpx.AsyncClient | None = None):
        self.api_key = api_key or ""
        self.client = client

    def available(self) -> bool:
        # 🔴 不再取决于有没有 Key：没有 Key 时走免密钥档。
        return True

    async def search(self, request: SearchRequest) -> SearchResponse:
        own = self.client is None
        client = self.client or httpx.AsyncClient(base_url="https://api.tavily.com", timeout=20)
        payload: dict[str, object] = {
            "query": request.query,
            "max_results": request.max_results,
            "include_content": True,
        }
        headers: dict[str, str] | None = None
        if self.api_key:
            # 带 Key 时保持既有形态（Key 放 body），不顺手改成 Bearer —— 那条路
            # 没有真实 Key 可验，改了等于拿一个能用的配置去赌一个没验证的写法。
            payload["api_key"] = self.api_key
        else:
            headers = {"X-Tavily-Access-Mode": "keyless"}
        try:
            response = await client.post("/search", json=payload, headers=headers)
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
