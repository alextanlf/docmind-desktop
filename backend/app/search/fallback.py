from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from app.api.errors import DomainError
from app.schemas.web_search import (
    SearchConnectionResult,
    SearchRequest,
    SearchResponse,
)
from app.search.provider import SearchProvider, provider_available

logger = logging.getLogger(__name__)

_LABELS = {
    "model": "模型内置联网",
    "tavily": "Tavily",
    # 🔴 下面三个来源的实现已经删除（抓屏不可维护），但**标签保留**：`web_search_runs`
    # 是持久表，老安装里的历史 run 仍然带着这些 provider 名。这是文案映射，不是平台
    # 分支 —— 删掉只会让历史记录显示成生字符串，没有任何收益。
    "bing": "免费搜索（Bing）",
    "searxng": "SearXNG",
    "duckduckgo": "免费搜索（DuckDuckGo）",
}


def _label(name: str) -> str:
    return _LABELS.get(name, name)


class FallbackSearchProvider:
    """Try each available search source in priority order."""

    name = "chain"

    def __init__(self, providers: Sequence[SearchProvider]) -> None:
        self.providers = list(providers)

    def available(self) -> bool:
        return any(provider_available(provider) for provider in self.providers)

    async def search(self, request: SearchRequest) -> SearchResponse:
        attempted: list[str] = []
        for provider in self.providers:
            if not provider_available(provider):
                continue
            attempted.append(provider.name)
            try:
                return await provider.search(request)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - one failing source must not abort the chain
                logger.warning("search provider %s failed: %s", provider.name, error)
                continue
        if not attempted:
            # 目前链上恒有 Tavily（免密钥即可用），所以这里到不了；留着是为了让
            # "换了一条没有兜底的链"这件事报成一个可读的错误，而不是静默空手而归。
            raise DomainError(
                "SEARCH_AUTH_FAILED",
                "没有可用的联网搜索来源",
                400,
                False,
                "稍后重试",
            )
        names = "、".join(_label(name) for name in attempted)
        raise DomainError(
            "SEARCH_PROVIDER_ERROR", f"联网搜索暂时不可用（已尝试：{names}）", 502, True
        )

    async def test_connection(self) -> SearchConnectionResult:
        for provider in self.providers:
            if not provider_available(provider):
                continue
            return await provider.test_connection()
        return SearchConnectionResult(
            ok=False, provider="none", message="没有可用的联网搜索来源"
        )
