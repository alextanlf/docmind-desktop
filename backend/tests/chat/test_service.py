from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

from app.api.errors import DomainError
from app.chat.service import ChatService
from app.core.llm import ChatDelta
from app.imports.events import InMemoryEventBroker
from app.schemas.chat import ChatStreamRequest
from app.schemas.retrieval import RetrievalHit, RetrievalResult
from app.storage.models import MessageRecord


class FakeRetriever:
    def __init__(self, result: RetrievalResult) -> None:
        self.result = result
        self.calls: list[tuple[str, list[str], int]] = []

    async def search(self, query: str, repository_ids: list[str], top_k: int = 5) -> RetrievalResult:
        self.calls.append((query, repository_ids, top_k))
        return self.result


class FakeLLM:
    def __init__(self, deltas: list[str] | None = None, error: DomainError | None = None) -> None:
        self.deltas = deltas or []
        self.error = error
        self.calls = []

    async def stream_chat(self, request):  # type: ignore[no-untyped-def]
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        for content in self.deltas:
            yield ChatDelta(content=content)


class FakeConversationStore:
    def __init__(self) -> None:
        self.messages: list[MessageRecord] = []

    def add_message(self, message: MessageRecord) -> MessageRecord:
        self.messages.append(message)
        return message

    def list_messages(self, session_id: str) -> list[MessageRecord]:
        return [message for message in self.messages if message.session_id == session_id]


def _hit() -> RetrievalHit:
    return RetrievalHit(
        chunk_id="chunk-1",
        document_id="doc-1",
        document_title="SwiftUI",
        text="@State 管理视图拥有的状态。",
        section_path="状态管理 > @State",
        page_number=None,
        source_url="https://docs.test/state",
        vector_score=0.9,
        keyword_score=1.0,
        fused_score=0.03,
    )


def _service(result: RetrievalResult, llm: FakeLLM) -> tuple[ChatService, FakeConversationStore]:
    store = FakeConversationStore()
    service = ChatService(
        retriever=FakeRetriever(result),
        llm=llm,
        conversation_store=store,
        event_broker=InMemoryEventBroker(),
    )
    return service, store


def _request(request_id: UUID | None = None) -> ChatStreamRequest:
    return ChatStreamRequest(
        request_id=request_id or uuid4(),
        session_id="s-1",
        message="@State 是什么？",
        repository_ids=["repo-1"],
    )


async def test_low_confidence_chat_persists_gap_without_calling_llm() -> None:
    llm = FakeLLM()
    service, store = _service(RetrievalResult(hits=[], max_score=0.4), llm)
    request = _request()

    events = [event async for event in service.stream(request)]

    assert [event.type for event in events] == ["citations", "delta", "done"]
    assert all(event.request_id == request.request_id for event in events)
    assert "当前文档未覆盖" in events[-2].payload["content"]
    assert llm.calls == []
    assert [(message.role, message.generation_status) for message in store.messages] == [
        ("user", "completed"),
        ("assistant", "completed"),
    ]
    assert json.loads(store.messages[-1].citations_json) == []


async def test_cited_chat_streams_nonempty_sanitized_deltas_and_persists_exact_answer() -> None:
    llm = FakeLLM(["依据文档 ", "", "https://evil.test/path ", "可使用 @State [S1] [S9]"])
    service, store = _service(RetrievalResult(hits=[_hit()], max_score=0.9), llm)

    events = [event async for event in service.stream(_request())]

    assert [event.type for event in events] == [
        "citations",
        "delta",
        "delta",
        "delta",
        "done",
    ]
    assert events[0].payload["citations"][0]["sourceId"] == "S1"
    streamed = "".join(event.payload["content"] for event in events if event.type == "delta")
    assert streamed == "依据文档  可使用 @State [S1] [S9]"
    assert store.messages[-1].content == streamed
    assert [item["sourceId"] for item in json.loads(store.messages[-1].citations_json)] == ["S1"]
    assert len(llm.calls[0].messages) <= 20


async def test_duplicate_request_id_starts_one_producer_and_persists_one_user_message() -> None:
    llm = FakeLLM(["回答 [S1]"])
    service, store = _service(RetrievalResult(hits=[_hit()], max_score=0.9), llm)
    request = _request()

    first, second = await asyncio.gather(
        _collect(service, request),
        _collect(service, request),
    )

    assert [event.sequence for event in first] == [1, 2, 3]
    assert [event.sequence for event in second] == [1, 2, 3]
    assert len([message for message in store.messages if message.role == "user"]) == 1
    assert len(llm.calls) == 1


async def test_closing_subscriber_does_not_cancel_owned_producer() -> None:
    release = asyncio.Event()

    class SlowLLM(FakeLLM):
        async def stream_chat(self, request):  # type: ignore[no-untyped-def]
            self.calls.append(request)
            await release.wait()
            yield ChatDelta(content="完成 [S1]")

    llm = SlowLLM()
    service, store = _service(RetrievalResult(hits=[_hit()], max_score=0.9), llm)
    stream = service.stream(_request())

    first = await anext(stream)
    assert first.type == "citations"
    await stream.aclose()
    release.set()
    await service.wait_for_idle()

    assert store.messages[-1].role == "assistant"
    assert store.messages[-1].content == "完成 [S1]"


async def test_provider_failure_persists_error_and_emits_one_structured_error() -> None:
    llm = FakeLLM(error=DomainError("MODEL_TIMEOUT", "模型服务响应超时", 504, True))
    service, store = _service(RetrievalResult(hits=[_hit()], max_score=0.9), llm)

    events = [event async for event in service.stream(_request())]

    assert [event.type for event in events] == ["citations", "error"]
    assert events[-1].payload == {
        "code": "MODEL_TIMEOUT",
        "message": "模型服务响应超时",
        "retryable": True,
    }
    assert store.messages[-1].generation_status == "error"


async def _collect(service: ChatService, request: ChatStreamRequest):  # type: ignore[no-untyped-def]
    return [event async for event in service.stream(request)]
