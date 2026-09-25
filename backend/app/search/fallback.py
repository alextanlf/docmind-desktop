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
            raise DomainError(
                "SEARCH_AUTH_FAILED",
                "没有可用的联网搜索来源，请配置搜索 API Key 或选择支持联网的模型",
                400,
                False,
                "配置搜索来源后重试",
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
