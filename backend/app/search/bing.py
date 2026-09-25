from __future__ import annotations

import base64
import binascii
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

_ENDPOINT = "https://www.bing.com/search"
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class BingProvider:
    """Keyless fallback that reads the Bing result page (reachable from mainland China)."""

    name = "bing"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self.client = client

    def available(self) -> bool:
        return True

    async def search(self, request: SearchRequest) -> SearchResponse:
        own = self.client is None
        client = self.client or httpx.AsyncClient(timeout=20, follow_redirects=True)
        try:
            response = await client.get(
                _ENDPOINT,
                params={
                    "q": request.query,
                    "count": "20",
                    "setlang": "zh-hans",
                    "mkt": "zh-CN",
                },
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
            )
        except httpx.TimeoutException as error:
            raise SearchProviderError("SEARCH_PROVIDER_TIMEOUT", "Bing 响应超时") from error
        except httpx.HTTPError as error:
            raise SearchProviderError("SEARCH_PROVIDER_ERROR", "Bing 请求失败") from error
        finally:
            if own:
                await client.aclose()
        if response.status_code >= 400:
            raise SearchProviderError("SEARCH_PROVIDER_ERROR", "Bing 暂时不可用")
        return _parse_results(response.text, request.max_results)

    async def test_connection(self) -> SearchConnectionResult:
        try:
            await self.search(SearchRequest(query="DocMind", max_results=1))
        except Exception:  # noqa: BLE001 - connection test reports failure instead of raising
            return SearchConnectionResult(ok=False, provider=self.name, message="Bing 连接失败")
        return SearchConnectionResult(ok=True, provider=self.name, message="Bing 可用")


def _parse_results(html: str, max_results: int) -> SearchResponse:
    soup = BeautifulSoup(html, "html.parser")
    results: list[NormalizedSearchResult] = []
    seen: set[str] = set()
    for node in soup.select("li.b_algo"):
        if len(results) >= max_results:
            break
        link = node.select_one("h2 a[href]") or node.select_one("a[href]")
        url = _canonical_url(link.get("href")) if link is not None else None
        if url is None or url in seen:
            continue
        title = link.get_text(" ", strip=True) or url
        snippet_node = node.select_one(".b_caption p") or node.select_one("p")
        snippet = snippet_node.get_text(" ", strip=True) if snippet_node is not None else ""
        seen.add(url)
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
        raise SearchProviderError("SEARCH_PROVIDER_ERROR", "Bing 没有返回结果")
    return SearchResponse(results=results, provider="bing")


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
    if parsed.netloc.endswith("bing.com") and parsed.path.startswith("/ck/a"):
        target = parse_qs(parsed.query).get("u", [None])[0]
        decoded = _decode_redirect(target)
        if decoded is None:
            return None
        value = decoded
        parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.netloc.endswith("bing.com") or parsed.netloc.endswith("microsofttranslator.com"):
        return None
    return value


def _decode_redirect(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    token = value.removeprefix("a1")
    try:
        decoded = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode(
            "utf-8", "replace"
        )
    except (binascii.Error, ValueError):
        return None
    if not decoded.startswith(("http://", "https://")):
        return None
    return decoded
