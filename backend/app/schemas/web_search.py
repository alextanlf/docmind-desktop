from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, HttpUrl

from app.schemas.common import WireModel


class SearchRequest(WireModel):
    query: str = Field(min_length=1, max_length=20_000)
    max_results: int = Field(default=5, ge=1, le=10)

class SearchRunRequest(WireModel):
    request_id: UUID
    session_id: UUID
    user_message_id: UUID
    query: str = Field(min_length=1, max_length=20_000)
    max_results: int = Field(default=5, ge=1, le=10)
    authorization_mode: Literal["auto", "explicit"] = "auto"
class SearchConnectionResult(WireModel):
    ok: bool
    provider: Literal["tavily"] = "tavily"
    message: str
class NormalizedSearchResult(WireModel):
    rank: int
    canonical_url: HttpUrl
    title: str = Field(max_length=512)
    snippet: str = Field(max_length=10_000)
    content: str = Field(max_length=50 * 1024)
class SearchResponse(WireModel):
    results: list[NormalizedSearchResult] = Field(max_length=10)
class WebSearchSettings(WireModel):
    provider: Literal["tavily"] = "tavily"
    mode: Literal["off", "ask", "auto"] = "ask"
    max_results: int = Field(default=5, ge=1, le=10)
    has_api_key: bool = False
class SearchResultView(NormalizedSearchResult):
    id: UUID
