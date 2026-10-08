from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.errors import DomainError
from app.schemas.web_search import (
    NormalizedSearchResult,
    SearchRequest,
    SearchResponse,
)
from app.search.service import SearchService
from app.storage.repositories import WebSearchRunStore


class StubProvider:
    name = "stub"

    def __init__(
        self,
        *,
        available: bool = True,
        provider: str = "model:dashscope",
        responses: dict[str, SearchResponse | Exception] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._available = available
        self._provider = provider
        self._responses = responses
        self._error = error
        self.queries: list[str] = []

    def available(self) -> bool:
        return self._available

    async def search(self, request: SearchRequest) -> SearchResponse:
        self.queries.append(request.query)
        if self._responses is not None:
            outcome = self._responses.get(request.query)
            if isinstance(outcome, Exception):
                raise outcome
            if outcome is not None:
                return outcome
            raise DomainError("SEARCH_PROVIDER_ERROR", "未配置的测试查询", 502, True)
        if self._error is not None:
            raise self._error
        return _response(self._provider, ["https://example.com/a"])



class StubPlanner:
    def __init__(self, queries: list[str]) -> None:
        self.queries = queries
        self.calls = 0

    async def plan(self, query: str, *, max_queries: int = 3) -> list[str]:
        self.calls += 1
        return [query, *self.queries][:max_queries]


def _response(provider: str, urls: list[str]) -> SearchResponse:
    return SearchResponse(
        provider=provider,
        results=[
            NormalizedSearchResult(
                rank=index,
                canonicalUrl=url,
                title=url,
                snippet="snippet",
                content="content",
            )
            for index, url in enumerate(urls, start=1)
        ],
    )


def _request(**overrides) -> SimpleNamespace:
    values = {
        "request_id": uuid4(),
        "session_id": uuid4(),
        "user_message_id": uuid4(),
        "query": "q",
        "max_results": 3,
        "query_rewrite": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_run_records_the_provider_that_served_it(database) -> None:
    store = WebSearchRunStore(database)
    service = SearchService(StubProvider(), store)

    view = await service.run(_request())

    assert view.status == "completed"
    assert view.provider == "model:dashscope"
    assert store.get(str(view.id)).provider == "model:dashscope"
    assert len(view.results) == 1


@pytest.mark.asyncio
async def test_run_without_available_provider_fails_before_creating_run(database) -> None:
    store = WebSearchRunStore(database)
    service = SearchService(StubProvider(available=False), store)

    with pytest.raises(DomainError) as error:
        await service.run(_request())

    assert error.value.code == "SEARCH_AUTH_FAILED"
    assert store.by_request_id(str(_request().request_id)) is None


@pytest.mark.asyncio
async def test_completed_run_is_replayed_even_without_available_provider(database) -> None:
    store = WebSearchRunStore(database)
    request = _request()
    first = await SearchService(StubProvider(), store).run(request)

    replayed = await SearchService(StubProvider(available=False), store).run(request)

    assert replayed.id == first.id
    assert replayed.status == "completed"
    assert replayed.provider == "model:dashscope"
    assert len(replayed.results) == 1


@pytest.mark.asyncio
async def test_query_rewrite_searches_every_variant_and_dedupes_results(database) -> None:
    provider = StubProvider(
        responses={
            "原问题": _response(
                "bing",
                ["https://example.com/a?utm_source=news", "https://example.com/b"],
            ),
            "变体一": _response("bing", ["https://example.com/c", "https://www.example.com/a/"]),
            "变体二": _response("bing", ["https://example.com/b"]),
        }
    )
    service = SearchService(
        provider,
        WebSearchRunStore(database),
        query_planner=StubPlanner(["变体一", "变体二"]),
    )

    view = await service.run(_request(query_rewrite=True, query="原问题"))

    assert len(view.results) == 3
    assert [str(item.canonical_url).split("?")[0].rstrip("/") for item in view.results] == [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ]
    assert [item.rank for item in view.results] == [1, 2, 3]
    assert set(provider.queries) == {"原问题", "变体一", "变体二"}
    assert view.provider == "bing"


@pytest.mark.asyncio
async def test_partial_query_failure_keeps_successful_results(database) -> None:
    provider = StubProvider(
        responses={
            "原问题": _response("tavily", ["https://example.com/a"]),
            "变体一": DomainError("SEARCH_PROVIDER_ERROR", "provider down", 502, True),
            "变体二": _response("tavily", ["https://example.com/c"]),
        }
    )
    service = SearchService(
        provider,
        WebSearchRunStore(database),
        query_planner=StubPlanner(["变体一", "变体二"]),
    )

    view = await service.run(_request(query_rewrite=True, query="原问题"))

    assert view.status == "completed"
    assert sorted(item.canonical_url for item in view.results) == [
        "https://example.com/a",
        "https://example.com/c",
    ]


@pytest.mark.asyncio
async def test_all_query_failures_mark_the_run_failed(database) -> None:
    provider = StubProvider(
        responses={
            "原问题": DomainError("SEARCH_PROVIDER_ERROR", "provider down", 502, True),
            "变体一": DomainError("SEARCH_PROVIDER_ERROR", "provider down", 502, True),
        }
    )
    store = WebSearchRunStore(database)
    service = SearchService(provider, store, query_planner=StubPlanner(["变体一"]))
    request = _request(query_rewrite=True, query="原问题")

    with pytest.raises(DomainError) as error:
        await service.run(request)

    assert error.value.code == "SEARCH_PROVIDER_ERROR"
    assert store.by_request_id(str(request.request_id)).status == "failed"


@pytest.mark.asyncio
async def test_query_rewrite_disabled_searches_the_original_query_only(database) -> None:
    provider = StubProvider()
    planner = StubPlanner(["变体一", "变体二"])
    service = SearchService(provider, WebSearchRunStore(database), query_planner=planner)

    await service.run(_request(query_rewrite=False))

    assert provider.queries == ["q"]
    assert planner.calls == 0


def test_complete_and_fail_both_reject_unknown_run(database) -> None:
    """complete() 与 fail() 必须对「运行不存在」给同一个 404，而不是崩溃。

    回归：complete() 少了 None 守卫，`run.provider = ...` 直接抛
    AttributeError('NoneType' has no attribute 'status') -> 500。
    """
    store = WebSearchRunStore(database)

    for call in (
        lambda: store.complete("missing-run", []),
        lambda: store.fail("missing-run", error_code="SEARCH_FAILED"),
    ):
        with pytest.raises(DomainError) as error:
            call()
        assert error.value.code == "SEARCH_RUN_NOT_FOUND"
        assert error.value.status_code == 404
