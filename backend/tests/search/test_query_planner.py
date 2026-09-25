from __future__ import annotations

import asyncio

import pytest

from app.core.llm import ChatDelta
from app.search.query_planner import LLMQueryPlanner


class StubLLM:
    def __init__(self, text: str = "", *, error: Exception | None = None, delay: float = 0) -> None:
        self.text = text
        self.error = error
        self.delay = delay
        self.requests = []

    async def stream_chat(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        if self.delay:
            await asyncio.sleep(self.delay)
        yield ChatDelta(content=self.text)


@pytest.mark.asyncio
async def test_plan_parses_json_array_and_keeps_original_first() -> None:
    llm = StubLLM('```json\n["DeepSeek V4 发布时间", "DeepSeek V4 release date"]\n```')
    planner = LLMQueryPlanner(llm)

    queries = await planner.plan("DeepSeek V4 什么时候发布的？")

    assert queries == [
        "DeepSeek V4 什么时候发布的？",
        "DeepSeek V4 发布时间",
        "DeepSeek V4 release date",
    ]
    assert "搜索查询规划器" in llm.requests[0].messages[0].content


@pytest.mark.asyncio
async def test_plan_drops_duplicates_and_caps_query_count() -> None:
    llm = StubLLM('好的：["原问题", "补充关键词", "third query", "fourth query"]')
    planner = LLMQueryPlanner(llm)

    queries = await planner.plan("原问题", max_queries=3)

    assert queries == ["原问题", "补充关键词", "third query"]


@pytest.mark.asyncio
async def test_plan_falls_back_to_original_on_invalid_output() -> None:
    planner = LLMQueryPlanner(StubLLM("抱歉，我不能帮你改写。"))

    assert await planner.plan("原问题") == ["原问题"]


@pytest.mark.asyncio
async def test_plan_falls_back_when_model_fails() -> None:
    planner = LLMQueryPlanner(StubLLM(error=RuntimeError("model down")))

    assert await planner.plan("原问题") == ["原问题"]


@pytest.mark.asyncio
async def test_plan_falls_back_when_model_times_out() -> None:
    planner = LLMQueryPlanner(StubLLM('["变体一"]', delay=0.3), timeout_seconds=0.05)

    assert await planner.plan("原问题") == ["原问题"]


@pytest.mark.asyncio
async def test_plan_skips_model_call_when_single_query_is_requested() -> None:
    llm = StubLLM('["变体一"]')
    planner = LLMQueryPlanner(llm)

    assert await planner.plan("原问题", max_queries=1) == ["原问题"]
    assert llm.requests == []
