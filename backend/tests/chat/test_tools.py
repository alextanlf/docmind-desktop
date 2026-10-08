from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.errors import DomainError
from app.chat.tools import ToolInvocation, ToolRegistry, WebSearchTool, parse_query
from app.core.llm import LLMToolCall


class Settings:
    def web_search(self):
        return SimpleNamespace(mode="auto", max_results=5, query_rewrite=False)


class Search:
    def __init__(self, error: DomainError | None = None) -> None:
        self.requests: list = []
        self.error = error

    async def run(self, request, **kwargs):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        result = SimpleNamespace(
            id=str(uuid4()),
            title="Web",
            snippet="snippet",
            content="content",
            canonical_url="https://example.test",
            created_at=datetime.now(UTC),
        )
        return SimpleNamespace(id=uuid4(), results=[result])


def invocation(**overrides) -> ToolInvocation:
    values = {
        "request_id": uuid4(),
        "session_id": "00000000-0000-0000-0000-000000000041",
        "user_message_id": "00000000-0000-0000-0000-000000000042",
        "authorization_mode": "auto",
        "round_index": 0,
    }
    values.update(overrides)
    return ToolInvocation(**values)


async def test_each_round_uses_a_distinct_search_request_id() -> None:
    """🔴 多轮搜索必须换 request_id。

    `WebSearchRunStore` 以 request_id 为唯一键，而 `SearchService.run` 对
    completed/running 的 run **直接返回旧结果**。沿用同一个 id 的话，第二轮搜索的
    query 会被静默丢弃、模型拿回第一轮的结果 —— 不报错，只是答错。
    """
    search = Search()
    tool = WebSearchTool(search, Settings())
    state = invocation()
    call = LLMToolCall(id="call_a", name="web_search", arguments='{"query":"first"}')

    first = await tool.execute(call, state)
    state.citation_offset += first.citations_registered
    state.round_index = 1
    await tool.execute(
        call.model_copy(update={"id": "call_b", "arguments": '{"query":"second"}'}), state
    )

    identifiers = [request.request_id for request in search.requests]
    assert identifiers[0] != identifiers[1]
    assert [request.query for request in search.requests] == ["first", "second"]


async def test_citation_ids_keep_advancing_across_rounds() -> None:
    """两轮搜索各自从 W1 起编号会互相覆盖，偏移量必须累计。"""
    search = Search()
    tool = WebSearchTool(search, Settings())
    state = invocation()
    call = LLMToolCall(id="call_a", name="web_search", arguments='{"query":"x"}')

    first = await tool.execute(call, state)
    state.citation_offset += first.citations_registered
    second = await tool.execute(call.model_copy(update={"id": "call_b"}), state)
    state.citation_offset += second.citations_registered

    assert first.citations_registered == 1
    assert sorted(state.registry) == ["W1", "W2"]
    assert "W1" in first.content and "W2" in second.content


async def test_unknown_tool_is_reported_back_without_being_fatal() -> None:
    """模型凭空造工具名是它自己的问题，不该让整条回答失败。"""
    outcome = await ToolRegistry([WebSearchTool(Search(), Settings())]).execute(
        LLMToolCall(id="call_a", name="teleport", arguments="{}"), invocation()
    )

    assert outcome.error_code == "TOOL_NOT_FOUND"
    assert outcome.fatal is False
    assert "teleport" in outcome.content


@pytest.mark.parametrize("arguments", ["", "not-json", "[]", '{"query": 3}', '{"q":"x"}'])
async def test_unusable_arguments_never_reach_the_search_service(arguments: str) -> None:
    """参数坏掉时连搜索都不该发起 —— 否则会白烧一次 provider 配额。"""
    search = Search()
    outcome = await WebSearchTool(search, Settings()).execute(
        LLMToolCall(id="call_a", name="web_search", arguments=arguments), invocation()
    )

    assert search.requests == []
    assert outcome.error_code == "SEARCH_INVALID_ARGUMENT"
    assert outcome.fatal is False


async def test_transport_failure_is_flagged_fatal() -> None:
    """空结果与"联网基础设施坏了"必须能区分：只有后者才值得惊动用户。"""
    error = DomainError("SEARCH_PROVIDER_ERROR", "搜索服务暂时不可用", 502, True)
    outcome = await WebSearchTool(Search(error), Settings()).execute(
        LLMToolCall(id="call_a", name="web_search", arguments='{"query":"x"}'), invocation()
    )

    assert outcome.error_code == "SEARCH_PROVIDER_ERROR"
    assert outcome.fatal is True
    assert outcome.citations_registered == 0


def test_parse_query_normalises_and_caps_length() -> None:
    assert parse_query('{"query":"  a   b  "}') == "a b"
    assert len(parse_query(json.dumps({"query": "x" * 900}))) == 500
    assert parse_query("") == ""
