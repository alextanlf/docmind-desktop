from __future__ import annotations

import httpx
import pytest

from app.schemas.web_search import SearchRequest
from app.search.provider import SearchProviderError
from app.search.searxng import SearxngProvider


@pytest.mark.asyncio
async def test_searxng_parses_json_results() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/searxng/search"
        assert request.url.params["q"] == "测试"
        assert request.url.params["format"] == "json"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://example.com/a",
                        "title": "结果 A",
                        "content": "结果 A 的摘要",
                    },
                    {"url": "https://example.com/b", "title": "结果 B", "content": "摘要 B"},
                    {"title": "缺少 URL", "content": "应被跳过"},
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = SearxngProvider("https://searx.example.com/searxng/", client=client)
    response = await provider.search(SearchRequest(query="测试", max_results=5))
    await client.aclose()

    assert provider.available() is True
    assert response.provider == "searxng"
    assert [str(item.canonical_url) for item in response.results] == [
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert [item.rank for item in response.results] == [1, 2]
    assert response.results[0].title == "结果 A"
    assert response.results[0].content == "结果 A 的摘要"


@pytest.mark.asyncio
async def test_searxng_reports_json_disabled_instances() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(403, text="forbidden"))
    )
    provider = SearxngProvider("https://searx.example.com", client=client)

    with pytest.raises(SearchProviderError) as error:
        await provider.search(SearchRequest(query="测试", max_results=5))

    assert "JSON" in error.value.message
    await client.aclose()


@pytest.mark.asyncio
async def test_searxng_reports_non_json_responses() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text="<html>search page</html>")
        )
    )
    provider = SearxngProvider("https://searx.example.com", client=client)

    with pytest.raises(SearchProviderError) as error:
        await provider.search(SearchRequest(query="测试", max_results=5))

    assert "JSON" in error.value.message
    await client.aclose()


@pytest.mark.asyncio
async def test_searxng_without_results_raises_provider_error() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"results": []}))
    )
    provider = SearxngProvider("https://searx.example.com", client=client)

    with pytest.raises(SearchProviderError):
        await provider.search(SearchRequest(query="测试", max_results=5))
    await client.aclose()
