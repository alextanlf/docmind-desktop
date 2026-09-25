from __future__ import annotations

import base64

import httpx
import pytest

from app.schemas.web_search import SearchRequest
from app.search.bing import BingProvider
from app.search.provider import SearchProviderError

_TARGET = "https://example.com/article"
_CLOAKED_TARGET = "https://example.com/other"
_CLOAKED = "a1" + base64.urlsafe_b64encode(_CLOAKED_TARGET.encode()).decode().rstrip("=")

_HTML = f"""
<html><body><ol id="b_results">
<li class="b_algo">
  <h2><a href="{_TARGET}">第一篇 结果</a></h2>
  <div class="b_caption"><p>第一段摘要内容。</p></div>
</li>
<li class="b_algo">
  <h2><a href="https://www.bing.com/ck/a?!&amp;&amp;p=abc&amp;u={_CLOAKED}&amp;ntb=1">Cloaked result</a></h2>
  <div class="b_caption"><p>Cloaked snippet.</p></div>
</li>
<li class="b_algo">
  <h2><a href="https://www.bing.com/search?q=more">Bing internal link</a></h2>
  <div class="b_caption"><p>Should be skipped.</p></div>
</li>
</ol></body></html>
"""


@pytest.mark.asyncio
async def test_bing_parses_results_and_unwraps_cloaked_links() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host in {"www.bing.com", "cn.bing.com"}
        assert request.url.params["q"] == "测试"
        return httpx.Response(200, text=_HTML)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = BingProvider(client=client)
    response = await provider.search(SearchRequest(query="测试", max_results=5))
    await client.aclose()

    assert provider.available() is True
    assert response.provider == "bing"
    assert [str(item.canonical_url) for item in response.results] == [
        _TARGET,
        _CLOAKED_TARGET,
    ]
    assert [item.rank for item in response.results] == [1, 2]
    assert response.results[0].title == "第一篇 结果"
    assert response.results[0].content == "第一段摘要内容。"


@pytest.mark.asyncio
async def test_bing_without_results_raises_provider_error() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="<html></html>"))
    )
    provider = BingProvider(client=client)
    with pytest.raises(SearchProviderError):
        await provider.search(SearchRequest(query="测试", max_results=5))
    await client.aclose()
