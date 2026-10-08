from __future__ import annotations

import logging

import httpx

from app.schemas.web_search import (
    NormalizedSearchResult,
    SearchRequest,
    SearchResponse,
)
from app.search.provider import SearchProviderError

logger = logging.getLogger(__name__)

# 历史 Key 的存储名。设置页的「联网搜索」分区已删除，这里保留读取是为了让
# 以前配过 Key 的安装继续用上自己的额度（付费档不限流），只是不再有输入口。
TAVILY_SECRET_NAME = "web-search:tavily"

_MAX_MESSAGE_CHARS = 300


def _error_message(response: httpx.Response) -> str | None:
    """从错误响应体里取一句能读的原因；取不到返回 None。

    两种形状都实测过：
      - 免密钥：`{"error": {"code": …, "message": …}}` —— 注意**不在 `detail` 下**
      - 带 Key：`{"detail": {"error": "…"}}`

    官方说 keyless 打满时返回"自然语言指令"，形态未公开；解析不出来时**不把原文直接
    当用户文案**（可能是 HTML 错误页），只记日志，由调用方给通用文案。
    """
    try:
        data = response.json()
    except ValueError:
        logger.warning(
            "tavily returned %s with a non-JSON body: %s",
            response.status_code,
            (response.text or "")[:200],
        )
        return None
    if not isinstance(data, dict):
        return None
    for candidate in (data.get("error"), data.get("detail")):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()[:_MAX_MESSAGE_CHARS]
        if isinstance(candidate, dict):
            for key in ("message", "error"):
                value = candidate.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()[:_MAX_MESSAGE_CHARS]
    return None


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
            # 🔴 `advanced` 买的不是"更多字"而是"更对题"：实测同一 query 下 content 只涨
            # 39%（3.0K → 4.2K），首位三条的相关性明显更好（0.79→0.86，把一条 marginal
            # 的 GitHub 仓库换成更对题的页面），代价约 +0.7s。带 Key 时 credit 从 1 涨到 2。
            #
            # ⚠️ 不要改用 `include_raw_content`：结果集与 `content` 完全相同，只多塞
            # 35.8K/次 的导航样板 markdown；agent loop 最多 3 轮 → 单条回答多 ~107K 字符。
            "search_depth": "advanced",
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
            raise SearchProviderError(
                "SEARCH_AUTH_FAILED", _error_message(response) or "Tavily API Key 无效"
            )
        if response.status_code == 429:
            raise SearchProviderError(
                "SEARCH_PROVIDER_RATE_LIMITED", _error_message(response) or "Tavily 请求过于频繁"
            )
        if response.status_code >= 400:
            # 🔴 必须把响应体里的原因透出来。免密钥档打满时 Tavily 返回的是**自然语言**
            # 指令（与带 Key 的 429 JSON 不是同一格式），而文案如果一律写成"暂时不可用"，
            # 用户看到的是"服务坏了"，实际是"免费额度用完了"。
            raise SearchProviderError(
                "SEARCH_PROVIDER_ERROR", _error_message(response) or "Tavily 暂时不可用"
            )
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

