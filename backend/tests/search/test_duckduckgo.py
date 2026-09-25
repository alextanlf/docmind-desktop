from __future__ import annotations

import httpx
import pytest

from app.schemas.web_search import SearchRequest
from app.search.duckduckgo import DuckDuckGoProvider
from app.search.provider import SearchProviderError

_HTML = """
<html><body>
<div class="result results_links">
  <h2 class="result__title">
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fone&amp;rut=abc">第一篇 结果</a>
  </h2>
  <a class="result__snippet">第一段摘要内容。</a>
</div>
<div class="result result--ad">
  <a class="result__a" href="//duckduckgo.com/y.js?ad_provider=ads">广告</a>
  <a class="result__snippet">广告摘要。</a>
</div>
<div class="result results_links">
  <h2 class="result__title">
    <a class="result__a" href="https://example.com/two">Second result</a>
  </h2>
  <a class="result__snippet">Second snippet.</a>
</div>
</body></html>
"""


@pytest.mark.asyncio
async def test_duckduckgo_parses_results_and_unwraps_redirects() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "html.duckduckgo.com"
        return httpx.Response(200, text=_HTML)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DuckDuckGoProvider(client=client)
    response = await provider.search(SearchRequest(query="测试", max_results=5))
    await client.aclose()

    assert provider.available() is True
    assert response.provider == "duckduckgo"
    assert [str(item.canonical_url) for item in response.results] == [
        "https://example.com/one",
        "https://example.com/two",
    ]
    assert response.results[0].title == "第一篇 结果"
    assert response.results[0].content == "第一段摘要内容。"
    assert [item.rank for item in response.results] == [1, 2]


@pytest.mark.asyncio
async def test_duckduckgo_without_results_raises_provider_error() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="<html></html>"))
    )
    provider = DuckDuckGoProvider(client=client)
    with pytest.raises(SearchProviderError):
        await provider.search(SearchRequest(query="测试", max_results=5))
    await client.aclose()
