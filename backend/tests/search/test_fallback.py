from __future__ import annotations

import pytest

from app.api.errors import DomainError
from app.schemas.web_search import NormalizedSearchResult, SearchRequest, SearchResponse
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


# --- 显式联网：本地来源降级为兜底 -----------------------------------------


class NamedProvider:
    def __init__(self, name: str, *, local: bool = False, results: bool = True) -> None:
        self.name = name
        self.local = local
        self._results = results
        self.calls = 0

    def available(self) -> bool:
        return True

    async def search(self, request: SearchRequest) -> SearchResponse:
        self.calls += 1
        if not self._results:
            raise SearchProviderError("SEARCH_PROVIDER_ERROR", "没有结果")
        return SearchResponse(
            results=[
                NormalizedSearchResult(
                    rank=1,
                    canonical_url=f"https://{self.name}.example/x",
                    title=self.name,
                    snippet="…",
                    content="…",
                )
            ],
            provider=self.name,
        )


@pytest.mark.asyncio
async def test_explicit_search_asks_the_web_before_the_local_corpus() -> None:
    """用户点「联网搜索」是他唯一能明确表达"我要去网上找"的动作。

    本地语料排在链首、有内容就胜出 —— 不按模式调序的话，这个动作会被本地语料悄悄
    吃掉：用户要的是网上的新信息，拿到的却是自己收藏的那几页。
    """
    local = NamedProvider("corpus", local=True)
    remote = NamedProvider("tavily")
    chain = FallbackSearchProvider([local, remote])

    hit = await chain.search(SearchRequest(query="x", authorization_mode="explicit"))

    assert hit.provider == "tavily"
    assert local.calls == 0


@pytest.mark.asyncio
async def test_auto_search_still_prefers_the_local_corpus() -> None:
    """反过来也要钉住：默认档位下本地优先（省额度、不出网、引用可复现）。"""
    local = NamedProvider("corpus", local=True)
    remote = NamedProvider("tavily")
    chain = FallbackSearchProvider([local, remote])

    hit = await chain.search(SearchRequest(query="x"))

    assert hit.provider == "corpus"
    assert remote.calls == 0


@pytest.mark.asyncio
async def test_the_local_corpus_is_a_fallback_not_deleted_in_explicit_mode() -> None:
    """上游全不可用时（模型没有内置联网、Tavily 额度打满），本地语料仍是最后一层。

    显式要求联网却彻底失败，比"用手里的语料作答"要糟 —— 所以是**降级**，不是排除。
    """
    local = NamedProvider("corpus", local=True)
    remote = NamedProvider("tavily", results=False)
    chain = FallbackSearchProvider([local, remote])

    hit = await chain.search(SearchRequest(query="x", authorization_mode="explicit"))

    assert hit.provider == "corpus"
    assert remote.calls == 1


def test_a_provider_without_the_local_flag_counts_as_remote() -> None:
    """默认必须是"远端"：新的搜索源多半依赖网络，默认成本地会让它意外插到显式联网
    的前面 —— 而那正是这条规则要避免的事。"""
    from app.search.fallback import _is_local

    class NoFlag:
        name = "x"

    assert _is_local(NoFlag()) is False
    assert _is_local(NamedProvider("corpus", local=True)) is True
