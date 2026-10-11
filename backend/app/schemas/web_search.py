from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, HttpUrl

from app.schemas.common import WireModel


class SearchRequest(WireModel):
    query: str = Field(min_length=1, max_length=20_000)
    max_results: int = Field(default=5, ge=1, le=10)
    #: 这一回合的联网授权档位。`explicit` = 用户点了「联网搜索」，也就是**明确要网上
    #: 的东西**；链据此把不依赖网络的来源挪到最后（见 `FallbackSearchProvider`）。
    #: 不影响上游厂商：它们只读 query 与 max_results。
    authorization_mode: Literal["auto", "explicit"] = "auto"

class SearchRunRequest(WireModel):
    request_id: UUID
    session_id: UUID
    user_message_id: UUID
    query: str = Field(min_length=1, max_length=20_000)
    max_results: int = Field(default=5, ge=1, le=10)
    query_rewrite: bool = True
    authorization_mode: Literal["auto", "explicit"] = "auto"
class NormalizedSearchResult(WireModel):
    rank: int
    canonical_url: HttpUrl
    title: str = Field(max_length=512)
    snippet: str = Field(max_length=10_000)
    content: str = Field(max_length=50 * 1024)
class SearchResponse(WireModel):
    results: list[NormalizedSearchResult] = Field(max_length=10)
    provider: str = ""
class SearchResultView(NormalizedSearchResult):
    id: UUID
