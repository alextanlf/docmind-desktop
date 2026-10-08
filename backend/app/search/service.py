from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

from app.api.errors import DomainError
from app.schemas.web_search import NormalizedSearchResult, SearchRequest, SearchResponse
from app.search.provider import provider_available
from app.storage.repositories import WebSearchRunStore

logger = logging.getLogger(__name__)

_MAX_QUERIES = 3
_RRF_K = 60
_MAX_QUERY_CHARS = 200
_TRACKING_KEYS = frozenset(
    {"fbclid", "gclid", "msclkid", "spm", "ref", "ref_src", "source", "from"}
)


@dataclass
class SearchRunView:
    id: UUID
    status: str
    results: list
    provider: str | None = None


class SearchService:
    def __init__(
        self,
        provider,
        run_store: WebSearchRunStore,
        *,
        query_planner=None,
    ):
        self.provider = provider
        self.run_store = run_store
        self.query_planner = query_planner

    async def run(self, request, *, authorization_mode: str = "auto") -> SearchRunView:
        if authorization_mode not in ("auto", "explicit"):
            raise DomainError("SEARCH_PERMISSION_DENIED", "搜索未获授权", 403)
        existing = self.run_store.by_request_id(str(request.request_id))
        if existing is not None and (
            existing.session_id != str(request.session_id)
            or existing.user_message_id != str(request.user_message_id)
        ):
            raise DomainError("SEARCH_REQUEST_CONFLICT", "请求标识已被其他会话使用", 409)
        if existing is not None and existing.status in ("completed", "running"):
            # A concurrent or retried request reuses the durable run; do not invoke provider twice.
            return SearchRunView(
                id=UUID(existing.id),
                status=existing.status,
                results=self.run_store.results(existing.id),
                provider=existing.provider,
            )
        if not provider_available(self.provider):
            raise DomainError(
                "SEARCH_AUTH_FAILED",
                "没有可用的联网搜索来源，请配置搜索 API Key 或选择支持联网的模型",
                400,
                False,
                "配置搜索来源后重试",
            )
        row = self.run_store.create(
            request_id=str(request.request_id),
            session_id=str(request.session_id),
            user_message_id=str(request.user_message_id),
            query=request.query,
        )
        max_results = getattr(request, "max_results", 5)
        queries = await self._plan_queries(request)
        try:
            responses = await self._search_queries(queries, max_results)
            results = _merge_results(responses, max_results)
            provider_label = _provider_label(responses)
            self.run_store.complete(row.id, results, provider=provider_label)
            return SearchRunView(
                id=UUID(row.id),
                status="completed",
                results=self.run_store.results(row.id),
                provider=provider_label or None,
            )
        except DomainError as error:
            self.run_store.fail(row.id, error_code=error.code)
            raise
        except Exception as exc:
            self.run_store.fail(row.id, error_code="SEARCH_PROVIDER_ERROR")
            raise DomainError("SEARCH_PROVIDER_ERROR", "搜索服务暂时不可用", 502, True) from exc

    async def _plan_queries(self, request) -> list[str]:
        query = " ".join(str(getattr(request, "query", "")).split())
        if self.query_planner is None or not bool(getattr(request, "query_rewrite", False)):
            return [query]
        try:
            planned = await self.query_planner.plan(query, max_queries=_MAX_QUERIES)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - planning failure falls back to the original query
            logger.warning("query planner failed: %s", error)
            return [query]
        queries: list[str] = []
        seen: set[str] = set()
        for item in planned:
            if not isinstance(item, str):
                continue
            value = " ".join(item.split())[:_MAX_QUERY_CHARS]
            if not value or value.casefold() in seen:
                continue
            seen.add(value.casefold())
            queries.append(value)
            if len(queries) >= _MAX_QUERIES:
                break
        if query and query.casefold() not in seen:
            queries = [query, *queries][:_MAX_QUERIES]
        return queries or [query]

    async def _search_queries(
        self, queries: list[str], max_results: int
    ) -> list[SearchResponse]:
        outcomes = await asyncio.gather(
            *(
                self.provider.search(SearchRequest(query=query, max_results=max_results))
                for query in queries
            ),
            return_exceptions=True,
        )
        for outcome in outcomes:
            if isinstance(outcome, asyncio.CancelledError):
                raise outcome
        responses = [outcome for outcome in outcomes if isinstance(outcome, SearchResponse)]
        if responses:
            if len(responses) < len(queries):
                logger.info("search partially failed: %s/%s queries", len(responses), len(queries))
            return responses
        error = next((o for o in outcomes if isinstance(o, DomainError)), None)
        if error is not None:
            raise error
        failure = next((o for o in outcomes if isinstance(o, Exception)), None)
        raise DomainError("SEARCH_PROVIDER_ERROR", "搜索服务暂时不可用", 502, True) from failure


def _merge_results(
    responses: list[SearchResponse], max_results: int
) -> list[NormalizedSearchResult]:
    scores: dict[str, float] = {}
    items: dict[str, NormalizedSearchResult] = {}
    first_seen: dict[str, int] = {}
    for response in responses:
        for item in response.results:
            key = _canonical_key(str(item.canonical_url))
            scores[key] = scores.get(key, 0.0) + 1.0 / (_RRF_K + item.rank)
            first_seen.setdefault(key, len(first_seen))
            current = items.get(key)
            if current is None or len(item.content) > len(current.content):
                items[key] = item
    ordered = sorted(items, key=lambda key: (-scores[key], first_seen[key]))
    return [
        items[key].model_copy(update={"rank": rank})
        for rank, key in enumerate(ordered[:max_results], start=1)
    ]


def _provider_label(responses: list[SearchResponse]) -> str:
    names = list(dict.fromkeys(response.provider for response in responses if response.provider))
    return "+".join(names)[:32]


def _canonical_key(url: str) -> str:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        if not host:
            return url.lower()
        port = f":{parsed.port}" if parsed.port else ""
        query = urlencode(
            [
                (key, value)
                for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_KEYS
            ]
        )
        path = parsed.path.rstrip("/") or "/"
        return urlunsplit((parsed.scheme.lower(), host + port, path, query, ""))
    except ValueError:
        return url.lower()
