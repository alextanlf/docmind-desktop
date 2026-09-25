from __future__ import annotations

import asyncio
import logging
import re

from bs4 import BeautifulSoup

from app.api.errors import DomainError
from app.document.parser import DocumentParser
from app.document.safe_http import SafeHttpClient
from app.schemas.imports import DownloadedDocument
from app.schemas.web_search import (
    NormalizedSearchResult,
)

logger = logging.getLogger(__name__)

SNIPPET_ONLY_PROVIDERS = frozenset({"bing", "duckduckgo", "searxng"})

_CONTENT_LIMIT = 50 * 1024
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
_FETCH_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


class ContentEnricher:
    """Fetch page content for snippet-only search results.

    Free engines return short snippets; fetching the top results turns them into
    evidence the RAG prompt can actually use. Failures keep the original snippet.
    """

    def __init__(
        self,
        client: SafeHttpClient,
        parser: DocumentParser | None = None,
        *,
        max_results: int = 3,
        max_bytes: int = 2 * 1024 * 1024,
        min_content_chars: int = 400,
        timeout_seconds: float = 8.0,
    ) -> None:
        self.client = client
        self.parser = parser or DocumentParser()
        self.max_results = max_results
        self.max_bytes = max_bytes
        self.min_content_chars = min_content_chars
        self.timeout_seconds = timeout_seconds

    async def enrich(
        self, results: list[NormalizedSearchResult]
    ) -> list[NormalizedSearchResult]:
        targets = [
            (index, result)
            for index, result in enumerate(results)
            if len(result.content.strip()) < self.min_content_chars
        ][: self.max_results]
        if not targets:
            return results
        fetched = await asyncio.gather(
            *(self._fetch(result) for _, result in targets), return_exceptions=True
        )
        enriched = list(results)
        for (index, result), content in zip(targets, fetched, strict=True):
            if isinstance(content, str) and content.strip():
                enriched[index] = result.model_copy(update={"content": content[:_CONTENT_LIMIT]})
        return enriched

    async def _fetch(self, result: NormalizedSearchResult) -> str | None:
        url = str(result.canonical_url)
        try:
            snapshot = await asyncio.wait_for(
                self.client.get(url, max_bytes=self.max_bytes, headers=_FETCH_HEADERS),
                timeout=self.timeout_seconds,
            )
        except (DomainError, TimeoutError):
            return None
        except Exception as error:  # noqa: BLE001 - enrichment is best-effort only
            logger.warning("search enrichment failed for %s: %s", url, error)
            return None
        if snapshot.media_type == "text/markdown":
            return snapshot.body.decode("utf-8", "ignore")
        if snapshot.media_type != "text/html":
            return None
        try:
            parsed = self.parser.parse(
                DownloadedDocument(
                    title=result.title,
                    source_url=url,
                    media_type="text/html",
                    raw_bytes=snapshot.body,
                )
            )
            return parsed.markdown
        except Exception as error:  # noqa: BLE001 - fall back to plain text extraction
            logger.warning("search enrichment parser fallback for %s: %s", url, error)
            return _plain_text(snapshot.body)


def _plain_text(body: bytes) -> str:
    soup = BeautifulSoup(body, "html.parser")
    for node in soup.select("script, style, nav, footer, aside, template, noscript"):
        node.decompose()
    text = soup.get_text("\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", text)
