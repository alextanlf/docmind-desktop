from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.errors import DomainError
from app.schemas.web_search import NormalizedSearchResult
from app.search.enrichment import ContentEnricher

_HTML = """
<html><head><title>页面标题</title></head>
<body><h1>页面标题</h1><p>这是抓取到的正文内容，用于验证增强逻辑。</p></body></html>
"""


class StubClient:
    def __init__(self, *, body: bytes = _HTML.encode(), media_type: str = "text/html", error=None):
        self.body = body
        self.media_type = media_type
        self.error = error
        self.calls: list[str] = []

    async def get(self, url: str, *, max_bytes: int, headers=None):
        self.calls.append(url)
        self.headers = headers
        if self.error is not None:
            raise self.error
        return SimpleNamespace(media_type=self.media_type, body=self.body, status_code=200)


def _result(url: str = "https://example.com/a", content: str = "短摘要") -> NormalizedSearchResult:
    return NormalizedSearchResult(
        rank=1,
        canonicalUrl=url,
        title="标题",
        snippet=content,
        content=content,
    )


@pytest.mark.asyncio
async def test_enrichment_replaces_short_snippets_with_page_content() -> None:
    client = StubClient()
    enricher = ContentEnricher(client)

    enriched = await enricher.enrich([_result()])

    assert client.calls == ["https://example.com/a"]
    assert "抓取到的正文内容" in enriched[0].content
    assert enriched[0].snippet == "短摘要"
    assert "Mozilla" in client.headers["User-Agent"]


@pytest.mark.asyncio
async def test_enrichment_skips_long_content_and_limits_fetches() -> None:
    client = StubClient()
    enricher = ContentEnricher(client, max_results=1, min_content_chars=10)
    results = [
        _result("https://example.com/long", "已经足够长的正文内容" * 10),
        _result("https://example.com/a"),
        _result("https://example.com/b"),
    ]

    await enricher.enrich(results)

    assert client.calls == ["https://example.com/a"]


@pytest.mark.asyncio
async def test_enrichment_keeps_snippet_when_fetch_fails() -> None:
    client = StubClient(error=DomainError("CRAWL_FETCH_FAILED", "网页抓取失败", 502, True))
    enricher = ContentEnricher(client)

    enriched = await enricher.enrich([_result()])

    assert enriched[0].content == "短摘要"


@pytest.mark.asyncio
async def test_enrichment_ignores_non_html_pages() -> None:
    client = StubClient(body=b"%PDF-1.4", media_type="application/pdf")
    enricher = ContentEnricher(client)

    enriched = await enricher.enrich([_result()])

    assert enriched[0].content == "短摘要"
