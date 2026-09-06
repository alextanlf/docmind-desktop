import httpx

from app.schemas.web_search import (
    NormalizedSearchResult,
    SearchConnectionResult,
    SearchRequest,
    SearchResponse,
)


class TavilyProvider:
    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None):
        self.api_key = api_key; self.client = client
    async def search(self, request: SearchRequest) -> SearchResponse:
        own = self.client is None; client = self.client or httpx.AsyncClient(base_url="https://api.tavily.com", timeout=20)
        try:
            r = await client.post("/search", json={"api_key": self.api_key, "query": request.query, "max_results": request.max_results, "include_content": True})
            r.raise_for_status(); data = r.json()
            out=[]
            for i,item in enumerate(data.get("results", [])[:request.max_results],1):
                out.append(NormalizedSearchResult(rank=i, canonical_url=item.get("url"), title=item.get("title", ""), snippet=item.get("snippet", ""), content=(item.get("content") or "")[:50*1024]))
            return SearchResponse(results=out)
        finally:
            if own: await client.aclose()
    async def test_connection(self) -> SearchConnectionResult:
        try:
            await self.search(SearchRequest(query="test", max_results=1)); return SearchConnectionResult(ok=True, message="ok")
        except (httpx.HTTPError, ValueError):
            return SearchConnectionResult(ok=False, message="connection failed")
