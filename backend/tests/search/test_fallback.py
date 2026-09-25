from __future__ import annotations

import pytest

from app.api.errors import DomainError
from app.schemas.web_search import (
    SearchConnectionResult,
    SearchRequest,
    SearchResponse,
)
from app.search.fallback import FallbackSearchProvider
from app.search.provider import SearchProviderError


class StubProvider:
    def __init__(self, name: str, *, available: bool = True, response=None, error=None):
        self.name = name
        self._available = available
        self._response = response
        self._error = error
        self.calls = 0

    def available(self) -> bool:
        return self._available

    async def search(self, request: SearchRequest) -> SearchResponse:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._response

    async def test_connection(self) -> SearchConnectionResult:
        return SearchConnectionResult(ok=True, provider=self.name, message="ok")


def _response(provider: str) -> SearchResponse:
    return SearchResponse.model_validate(
        {
            "provider": provider,
            "results": [
                {
                    "rank": 1,
                    "canonicalUrl": "https://example.com/a",
                    "title": "A",
                    "snippet": "snippet",
                    "content": "content",
                }
            ],
        }
    )


@pytest.mark.asyncio
async def test_chain_uses_first_available_provider() -> None:
    model = StubProvider("model", response=_response("model:dashscope"))
    tavily = StubProvider("tavily", response=_response("tavily"))
    chain = FallbackSearchProvider([model, tavily])

    result = await chain.search(SearchRequest(query="q"))

    assert result.provider == "model:dashscope"
    assert model.calls == 1
    assert tavily.calls == 0


@pytest.mark.asyncio
async def test_chain_falls_through_failed_and_unavailable_providers() -> None:
    model = StubProvider(
        "model", error=SearchProviderError("SEARCH_PROVIDER_ERROR", "model down")
    )
    tavily = StubProvider("tavily", available=False)
    free = StubProvider("duckduckgo", response=_response("duckduckgo"))
    chain = FallbackSearchProvider([model, tavily, free])

    result = await chain.search(SearchRequest(query="q"))

    assert result.provider == "duckduckgo"
    assert model.calls == 1
    assert tavily.calls == 0
    assert free.calls == 1


@pytest.mark.asyncio
async def test_chain_reports_auth_error_without_any_provider() -> None:
    chain = FallbackSearchProvider([StubProvider("model", available=False)])
    with pytest.raises(DomainError) as error:
        await chain.search(SearchRequest(query="q"))
    assert error.value.code == "SEARCH_AUTH_FAILED"
    assert chain.available() is False


@pytest.mark.asyncio
async def test_chain_reports_attempted_sources_when_all_fail() -> None:
    chain = FallbackSearchProvider(
        [
            StubProvider("model", error=SearchProviderError("SEARCH_PROVIDER_ERROR", "model down")),
            StubProvider("tavily", error=SearchProviderError("SEARCH_PROVIDER_ERROR", "tavily down")),
        ]
    )
    with pytest.raises(DomainError) as error:
        await chain.search(SearchRequest(query="q"))
    assert error.value.code == "SEARCH_PROVIDER_ERROR"
    assert "模型内置联网" in error.value.message
    assert "Tavily" in error.value.message
