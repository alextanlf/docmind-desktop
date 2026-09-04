from typing import Protocol

from app.schemas.web_search import SearchConnectionResult, SearchRequest, SearchResponse


class SearchProvider(Protocol):
    async def test_connection(self) -> SearchConnectionResult: ...
    async def search(self, request: SearchRequest) -> SearchResponse: ...
