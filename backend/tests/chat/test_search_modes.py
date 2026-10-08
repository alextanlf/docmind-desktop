from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.chat.service import ChatService
from app.chat.tools import ToolRegistry, WebSearchTool
from app.core.llm import ChatDelta, LLMToolCall, LLMToolCallDelta
from app.imports.events import InMemoryEventBroker
from app.schemas.chat import ChatStreamRequest
from app.schemas.retrieval import RetrievalResult
from tests.chat.test_service import FakeConversationStore, FakeLLM, FakeRetriever


class Settings:
    def __init__(self, mode):
        self.mode = mode

    def web_search(self):
        return SimpleNamespace(mode=self.mode, max_results=5, query_rewrite=False)


class Search:
    def __init__(self):
        self.calls = 0

    async def run(self, request, **kwargs):
        self.calls += 1
        result = SimpleNamespace(
            id=str(uuid4()),
            title="Web",
            snippet="evidence",
            content="evidence",
            canonical_url="https://example.test",
            created_at=datetime.now(UTC),
        )
        return SimpleNamespace(id=uuid4(), results=[result])


class Store(FakeConversationStore):
    def add_message(self, message):
        if message.id is None:
            message.id = str(uuid4())
        return super().add_message(message)

    def get_message(self, message_id):
        return next((message for message in self.messages if message.id == message_id), None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "calls", "suggested", "offers_tools"),
    [("off", 0, False, False), ("ask", 0, True, False), ("auto", 0, False, True)],
)
async def test_low_evidence_respects_search_mode(mode, calls, suggested, offers_tools):
    """auto 档不再预检索 —— 它只把工具发给模型，搜不搜由模型决定。"""
    search = Search()
    settings = Settings(mode)
    llm = FakeLLM(["web [W1]"])
    service = ChatService(
        retriever=FakeRetriever(RetrievalResult(hits=[], max_score=0)),
        llm=llm,
        conversation_store=Store(),
        event_broker=InMemoryEventBroker(retention=None),
        search_service=search,
        settings_service=settings,
        tool_registry=ToolRegistry([WebSearchTool(search, settings)]),
    )
    events = [
        event
        async for event in service.stream(
            ChatStreamRequest(
                request_id=uuid4(),
                session_id="00000000-0000-0000-0000-000000000041",
                message="question",
                repository_ids=["00000000-0000-0000-0000-000000000042"],
            )
        )
    ]
    assert search.calls == calls
    assert events[-1].payload.get("searchSuggested", False) is suggested
    if offers_tools:
        # 🔴 本次改动的核心：闸门从"相似度阈值"换成"模型自己决定"。所以必须验证
        # 工具确实下发到了 —— 否则 auto 档会静默地永远不联网（模型无从知道能搜）。
        assert llm.calls, "auto 档必须把工具发给模型"
        assert [tool.name for tool in llm.calls[0].tools] == ["web_search"]
    else:
        # off / ask 档：既没有本地证据也不许联网取证，连模型都不该被叫到。
        assert llm.calls == []


@pytest.mark.asyncio
async def test_auto_mode_searches_when_the_model_asks():
    """模型主动调用工具时，搜索真的发生、引用真的注册、过程真的透出。"""
    search = Search()
    settings = Settings("auto")
    service = ChatService(
        retriever=FakeRetriever(RetrievalResult(hits=[], max_score=0)),
        llm=FakeLLM(
            ["web [W1]"],
            tool_calls=[
                LLMToolCall(id="call-1", name="web_search", arguments='{"query":"question"}')
            ],
        ),
        conversation_store=Store(),
        event_broker=InMemoryEventBroker(retention=None),
        search_service=search,
        settings_service=settings,
        tool_registry=ToolRegistry([WebSearchTool(search, settings)]),
    )
    events = [
        event
        async for event in service.stream(
            ChatStreamRequest(
                request_id=uuid4(),
                session_id="00000000-0000-0000-0000-000000000041",
                message="question",
                repository_ids=["00000000-0000-0000-0000-000000000042"],
            )
        )
    ]
    assert search.calls == 1
    assert events[-1].type == "done"
    # 工具生命周期要透出，否则用户看不到"正在联网搜索"。
    assert [
        event.payload["status"]
        for event in events
        if event.type == "progress" and event.payload.get("stage") == "tool"
    ] == ["running", "done"]
    # W1 由工具轮注册（首个 citations 事件发在生成之前，不可能包含它），
    # 所以必须靠生成后的补发事件带出来。
    registered = [
        item.get("sourceId")
        for event in events
        if event.type == "citations"
        for item in event.payload["citations"]
    ]
    assert "W1" in registered
    # 工具结果必须以 role=tool + tool_call_id 回灌，否则厂商无法把结果配对到调用。
    follow_up = service.llm.calls[1]
    assert follow_up.messages[-1].role == "tool"
    assert follow_up.messages[-1].tool_call_id == "call-1"
    assert "<web-result>" in follow_up.messages[-1].content


@pytest.mark.asyncio
async def test_tool_loop_is_bounded_and_the_last_call_gets_no_tools():
    """模型每轮都要求工具时的兜底：轮数有上限，且最后一轮收走工具逼它作答。

    没有这个上限，一个"总想再搜一次"的模型会让请求永远不返回。
    """
    search = Search()
    settings = Settings("auto")

    class EndlessLLM:
        def __init__(self) -> None:
            self.calls = []

        async def stream_chat(self, request):  # type: ignore[no-untyped-def]
            self.calls.append(request)
            if request.tools:
                yield ChatDelta(
                    tool_calls=[
                        LLMToolCallDelta(
                            index=0,
                            id=f"call-{len(self.calls)}",
                            name="web_search",
                            arguments='{"query":"q"}',
                        )
                    ]
                )
                return
            yield ChatDelta(content="收尾回答")

    llm = EndlessLLM()
    service = ChatService(
        retriever=FakeRetriever(RetrievalResult(hits=[], max_score=0)),
        llm=llm,
        conversation_store=Store(),
        event_broker=InMemoryEventBroker(retention=None),
        search_service=search,
        settings_service=settings,
        tool_registry=ToolRegistry([WebSearchTool(search, settings)]),
    )
    events = [
        event
        async for event in service.stream(
            ChatStreamRequest(
                request_id=uuid4(),
                session_id="00000000-0000-0000-0000-000000000041",
                message="question",
                repository_ids=["00000000-0000-0000-0000-000000000042"],
            )
        )
    ]

    assert events[-1].type == "done"
    assert [event.payload["content"] for event in events if event.type == "delta"] == ["收尾回答"]
    # 3 轮工具 + 1 次收尾调用；收尾那次不带工具，所以模型只能给出文字。
    assert search.calls == 3
    assert len(llm.calls) == 4
    assert llm.calls[-1].tools == []


@pytest.mark.asyncio
async def test_explicit_continuation_reuses_original_user_message():
    search = Search()
    store = Store()
    service = ChatService(
        retriever=FakeRetriever(RetrievalResult(hits=[], max_score=0)),
        llm=FakeLLM(["web [W1]"]),
        conversation_store=store,
        event_broker=InMemoryEventBroker(retention=None),
        search_service=search,
        settings_service=Settings("ask"),
    )
    session_id = "00000000-0000-0000-0000-000000000041"
    repositories = ["00000000-0000-0000-0000-000000000042"]
    first = [
        event
        async for event in service.stream(
            ChatStreamRequest(
                request_id=uuid4(),
                session_id=session_id,
                message="question",
                repository_ids=repositories,
            )
        )
    ]
    user_message_id = first[-1].payload["userMessageId"]
    second = [
        event
        async for event in service.stream(
            ChatStreamRequest(
                request_id=uuid4(),
                session_id=session_id,
                message="question",
                repository_ids=repositories,
                web_search_permission="explicit",
                existing_user_message_id=user_message_id,
            )
        )
    ]
    assert len([message for message in store.messages if message.role == "user"]) == 1
    assert search.calls == 1
    assert any(item.get("sourceId") == "W1" for item in second[0].payload["citations"])
