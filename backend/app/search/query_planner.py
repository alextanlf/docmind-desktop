from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Protocol

from app.core.llm import ChatRequest, LLMMessage, LLMProvider

logger = logging.getLogger(__name__)

_MAX_QUERY_CHARS = 200
_JSON_ARRAY = re.compile(r"\[.*?\]", re.DOTALL)


class QueryPlanner(Protocol):
    async def plan(self, query: str, *, max_queries: int = 3) -> list[str]: ...


class LLMQueryPlanner:
    """Rewrite a user question into a few search-engine queries via the chat model."""

    def __init__(self, llm: LLMProvider, *, timeout_seconds: float = 15.0) -> None:
        self.llm = llm
        self.timeout_seconds = timeout_seconds

    async def plan(self, query: str, *, max_queries: int = 3) -> list[str]:
        original = " ".join(query.split())
        if max_queries <= 1 or not original:
            return [original]
        try:
            text = await asyncio.wait_for(self._complete(original, max_queries), self.timeout_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - planning must never block the search
            logger.warning("search query planning failed: %s", error)
            return [original]
        variants = _parse_queries(text, original, max_queries)
        return variants or [original]

    async def _complete(self, query: str, max_queries: int) -> str:
        request = ChatRequest(
            messages=[LLMMessage(role="user", content=_build_prompt(query, max_queries))],
            temperature=0,
        )
        parts: list[str] = []
        async for delta in self.llm.stream_chat(request):
            parts.append(delta.content)
        return "".join(parts)


def _build_prompt(query: str, max_queries: int) -> str:
    return (
        f"你是搜索查询规划器。把用户问题改写成最多 {max_queries} 条适合搜索引擎的查询词。\n"
        "要求：\n"
        "1. 第一条查询尽量贴近原问题；\n"
        "2. 其他查询可以从不同角度补充关键词、同义词或英文术语；\n"
        "3. 只输出查询词，不要回答问题，也不要解释；\n"
        '4. 只输出 JSON 数组，例如 ["查询1", "查询2"]。\n\n'
        f"用户问题：{query}"
    )


def _parse_queries(text: str, original: str, max_queries: int) -> list[str]:
    match = _JSON_ARRAY.search(text)
    if match is None:
        return []
    try:
        payload = json.loads(match.group(0))
    except ValueError:
        return []
    if not isinstance(payload, list):
        return []
    queries: list[str] = []
    seen = {original.casefold()}
    for item in payload:
        if not isinstance(item, str):
            continue
        value = " ".join(item.split())[:_MAX_QUERY_CHARS]
        if not value or value.casefold() in seen:
            continue
        seen.add(value.casefold())
        queries.append(value)
        if len(queries) >= max_queries - 1:
            break
    return [original, *queries] if queries else []
