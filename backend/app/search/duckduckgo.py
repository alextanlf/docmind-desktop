from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import httpx
from bs4 import BeautifulSoup

from app.schemas.web_search import (
    NormalizedSearchResult,
    SearchConnectionResult,
    SearchRequest,
    SearchResponse,
)
from app.search.provider import SearchProviderError

_ENDPOINT = "https://html.duckduckgo.com/html/"
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class DuckDuckGoProvider:
    """Keyless fallback that scrapes the DuckDuckGo HTML endpoint."""

    name = "duckduckgo"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self.client = client

    def available(self) -> bool:
        return True

    async def search(self, request: SearchRequest) -> SearchResponse:
        own = self.client is None
        client = self.client or httpx.AsyncClient(timeout=20)
        try:
            response = await client.post(
                _ENDPOINT,
                data={"q": request.query, "kl": "cn-zh"},
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
            )
        except httpx.TimeoutException as error:
            raise SearchProviderError("SEARCH_PROVIDER_TIMEOUT", "免费搜索响应超时") from error
        except httpx.HTTPError as error:
            raise SearchProviderError("SEARCH_PROVIDER_ERROR", "免费搜索请求失败") from error
        finally:
            if own:
                await client.aclose()
        if response.status_code >= 400:
            raise SearchProviderError("SEARCH_PROVIDER_ERROR", "免费搜索暂时不可用")
        return _parse_results(response.text, request.max_results)

    async def test_connection(self) -> SearchConnectionResult:
        try:
            await self.search(SearchRequest(query="DocMind", max_results=1))
        except Exception:  # noqa: BLE001 - connection test reports failure instead of raising
            return SearchConnectionResult(
                ok=False, provider=self.name, message="免费搜索连接失败"
            )
        return SearchConnectionResult(ok=True, provider=self.name, message="免费搜索可用")


def _parse_results(html: str, max_results: int) -> SearchResponse:
    soup = BeautifulSoup(html, "html.parser")
    results: list[NormalizedSearchResult] = []
    for node in soup.select("div.result"):
        if len(results) >= max_results:
            break
        link = node.select_one("a.result__a")
        if link is None:
            continue
        url = _canonical_url(link.get("href"))
        if url is None:
            continue
        title = link.get_text(" ", strip=True) or url
        snippet_node = node.select_one(".result__snippet")
        snippet = snippet_node.get_text(" ", strip=True) if snippet_node is not None else ""
        try:
            results.append(
                NormalizedSearchResult(
                    rank=len(results) + 1,
                    canonical_url=url,
                    title=title[:512],
                    snippet=snippet[:10_000],
                    content=snippet[: 50 * 1024],
                )
            )
        except ValueError:
            continue
    if not results:
        raise SearchProviderError("SEARCH_PROVIDER_ERROR", "免费搜索没有返回结果")
    return SearchResponse(results=results, provider="duckduckgo")


def _canonical_url(raw: object) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    value = raw.strip()
    if value.startswith("//"):
        value = f"https:{value}"
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg")
        if not target:
            return None
        value = target[0]
        parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.netloc.endswith("duckduckgo.com"):
        return None
    return value
