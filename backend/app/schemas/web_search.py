from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, HttpUrl, field_validator

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
    query_rewrite: bool = True
    authorization_mode: Literal["auto", "explicit"] = "auto"
class SearchConnectionResult(WireModel):
    ok: bool
    provider: str = "tavily"
    message: str
class NormalizedSearchResult(WireModel):
    rank: int
    canonical_url: HttpUrl
    title: str = Field(max_length=512)
    snippet: str = Field(max_length=10_000)
    content: str = Field(max_length=50 * 1024)
class SearchResponse(WireModel):
    results: list[NormalizedSearchResult] = Field(max_length=10)
    provider: str = ""
def normalize_searxng_url(value: str) -> str:
    """Validate a user-supplied SearXNG instance URL; empty means "not configured"."""
    value = (value or "").strip()
    if not value:
        return ""
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("invalid SearXNG instance URL")
    return value.rstrip("/")


class WebSearchSettings(WireModel):
    mode: Literal["off", "ask", "auto"] = "ask"
    max_results: int = Field(default=5, ge=1, le=10)
    query_rewrite: bool = True
    searxng_url: str = ""
    has_api_key: bool = False

    @field_validator("searxng_url")
    @classmethod
    def _normalize_searxng_url(cls, value: str) -> str:
        return normalize_searxng_url(value)
    model_search_available: bool = False
    model_search_label: str = ""
    free_fallback_available: bool = True
class SearchResultView(NormalizedSearchResult):
    id: UUID
