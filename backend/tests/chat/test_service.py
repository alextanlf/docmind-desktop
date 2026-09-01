from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from types import SimpleNamespace
from uuid import UUID, uuid4

from app.api.errors import DomainError
from app.chat.service import ChatService
from app.core.llm import ChatDelta
from app.imports.events import InMemoryEventBroker
from app.schemas.chat import ChatStreamRequest
from app.schemas.retrieval import RetrievalHit, RetrievalResult
from app.storage.database import Database
from app.storage.models import MessageRecord, SessionRecord
from app.storage.repositories import ConversationStore


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
        self.chat_requests: dict[str, SimpleNamespace] = {}

    def add_message(self, message: MessageRecord) -> MessageRecord:
        self.messages.append(message)
        return message

    def list_messages(self, session_id: str) -> list[MessageRecord]:
        return [message for message in self.messages if message.session_id == session_id]

    def claim_chat_request(self, request_id: str, session_id: str) -> tuple[SimpleNamespace, bool]:
        record = self.chat_requests.get(request_id)
        if record is not None:
            return record, False
        record = SimpleNamespace(
            request_id=request_id,
            session_id=session_id,
            terminal_type=None,
            terminal_payload_json=None,
        )
        self.chat_requests[request_id] = record
        return record, True

    def complete_chat_request(
        self, request_id: str, terminal_type: str, payload: dict[str, object]
    ) -> SimpleNamespace:
        record = self.chat_requests[request_id]
        record.terminal_type = terminal_type
        record.terminal_payload_json = json.dumps(payload, ensure_ascii=False)
        return record


class FailingConversationStore(FakeConversationStore):
    def __init__(
        self,
        *,
        fail_user: bool = False,
        fail_assistant_status: str | None = None,
    ) -> None:
        super().__init__()
        self.fail_user = fail_user
        self.fail_assistant_status = fail_assistant_status
        self.add_attempts: list[tuple[str, str | None]] = []

    def add_message(self, message: MessageRecord) -> MessageRecord:
        self.add_attempts.append((message.role, message.generation_status))
        if message.role == "user" and self.fail_user:
            raise RuntimeError("user persistence failed")
        if (
            message.role == "assistant"
            and message.generation_status == self.fail_assistant_status
        ):
            raise RuntimeError("assistant persistence failed")
        return super().add_message(message)


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
        event_broker=InMemoryEventBroker(retention=None),
    )
    return service, store


def _service_with_store(
    result: RetrievalResult,
    llm: FakeLLM,
    store: FakeConversationStore,
    *,
    unlimited_events: bool = False,
) -> ChatService:
    return ChatService(
        retriever=FakeRetriever(result),
        llm=llm,
        conversation_store=store,
        event_broker=(
            InMemoryEventBroker(retention=None)
            if unlimited_events
            else InMemoryEventBroker()
        ),
    )


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


async def test_url_path_punctuation_is_identical_in_stream_and_persisted_answer() -> None:
    llm = FakeLLM(
        [
            "https://evil.test/path",
            ".Next sentence [S1] ",
            "https://evil.test/path",
            ":123 后续",
        ]
    )
    service, store = _service(RetrievalResult(hits=[_hit()], max_score=0.9), llm)

    events = [event async for event in service.stream(_request())]

    streamed = "".join(
        event.payload["content"] for event in events if event.type == "delta"
    )
    assert streamed == ".Next sentence [S1] :123 后续"
    assert store.messages[-1].content == streamed


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


async def test_stop_reaps_named_subscription_helpers_for_active_subscriber() -> None:
    release = asyncio.Event()

    class SlowLLM(FakeLLM):
        async def stream_chat(self, request):  # type: ignore[no-untyped-def]
            self.calls.append(request)
            await release.wait()
            yield ChatDelta(content="完成 [S1]")

    service, _ = _service(
        RetrievalResult(hits=[_hit()], max_score=0.9), SlowLLM()
    )
    request = _request()
    stream = service.stream(request)
    assert (await anext(stream)).type == "citations"
    reader = asyncio.create_task(anext(stream), name="test-chat-stream-reader")
    await asyncio.sleep(0)
    helpers = _pending_stream_helpers(exclude={reader})

    try:
        assert {task.get_name() for task in helpers} == {
            f"chat-stream:{request.request_id}:next-event",
            f"chat-stream:{request.request_id}:fallback-ready",
        }

        await service.stop()
        with suppress(asyncio.CancelledError, StopAsyncIteration):
            await asyncio.wait_for(reader, timeout=0.2)

        assert reader.done()
        assert _pending_stream_helpers(exclude={reader}) == []
    finally:
        release.set()
        if not reader.done():
            reader.cancel()
        with suppress(asyncio.CancelledError, StopAsyncIteration):
            await reader
        leaked = _pending_stream_helpers()
        for task in leaked:
            task.cancel()
        if leaked:
            await asyncio.gather(*leaked, return_exceptions=True)
        await service.stop()


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


async def test_slow_and_late_subscribers_replay_lossless_long_answer() -> None:
    deltas = [f"片段{index}|" for index in range(150)] + ["结论 [S1]"]
    llm = FakeLLM(deltas)
    store = FakeConversationStore()
    broker = InMemoryEventBroker(retention=None)
    service = ChatService(
        retriever=FakeRetriever(RetrievalResult(hits=[_hit()], max_score=0.9)),
        llm=llm,
        conversation_store=store,
        event_broker=broker,
        terminal_replay_ttl_seconds=0.05,
    )
    request = _request()
    slow_stream = service.stream(request)

    first = await anext(slow_stream)
    assert first.type == "citations"
    await service.wait_for_idle()
    cleanup = service._cleanup_tasks[str(request.request_id)]
    await asyncio.sleep(0.06)
    assert not cleanup.done()
    assert str(request.request_id) in broker._jobs

    slow_events = [first, *[event async for event in slow_stream]]
    replayed_events = [event async for event in service.stream(request)]
    resumed_events = [
        event async for event in service.stream(request, after_sequence=first.sequence)
    ]

    expected_sequences = list(range(1, len(deltas) + 3))
    assert [event.sequence for event in slow_events] == expected_sequences
    assert [event.sequence for event in replayed_events] == expected_sequences
    assert slow_events[0].type == replayed_events[0].type == "citations"
    streamed = "".join(
        event.payload["content"] for event in slow_events if event.type == "delta"
    )
    replayed = "".join(
        event.payload["content"] for event in replayed_events if event.type == "delta"
    )
    assert streamed == replayed == store.messages[-1].content
    assert slow_events[-1].type == replayed_events[-1].type == "done"
    assert [event.sequence for event in resumed_events] == expected_sequences[1:]
    assert len(llm.calls) == 1

    await asyncio.wait_for(cleanup, timeout=0.3)
    _assert_chat_archives_empty(service, broker)


async def test_expired_terminal_replay_never_regenerates_detached_request() -> None:
    """Catches cleanup forgetting terminal request ownership before a remount."""
    llm = FakeLLM(["完成 [S1]"])
    database = Database("sqlite+pysqlite:///:memory:")
    database.upgrade()
    with database.session() as session:
        session.add(SessionRecord(id="s-1"))
    store = ConversationStore(database)
    broker = InMemoryEventBroker(retention=None)
    service = ChatService(
        retriever=FakeRetriever(RetrievalResult(hits=[_hit()], max_score=0.9)),
        llm=llm,
        conversation_store=store,
        event_broker=broker,
        terminal_replay_ttl_seconds=0.01,
    )
    request = _request()
    detached_stream = service.stream(request)

    first = await anext(detached_stream)
    assert first.type == "citations"
    await detached_stream.aclose()
    await service.wait_for_idle()
    cleanup = service._cleanup_tasks[str(request.request_id)]
    await asyncio.wait_for(cleanup, timeout=0.2)
    assert str(request.request_id) not in broker._jobs

    try:
        remounted = [
            event
            async for event in service.stream(request, after_sequence=first.sequence)
        ]

        assert [event.type for event in remounted] == ["done"]
        assert remounted[0].sequence > first.sequence
        assert len(
            [message for message in store.list_messages(request.session_id) if message.role == "user"]
        ) == 1
        assert len(llm.calls) == 1
    finally:
        await service.stop()
        database.engine.dispose()


async def test_stop_cancels_terminal_cleanup_and_clears_chat_archives() -> None:
    broker = InMemoryEventBroker(retention=None)
    service = ChatService(
        retriever=FakeRetriever(RetrievalResult(hits=[_hit()], max_score=0.9)),
        llm=FakeLLM(["回答 [S1]"]),
        conversation_store=FakeConversationStore(),
        event_broker=broker,
        terminal_replay_ttl_seconds=60,
    )
    request = _request()

    await _collect(service, request)
    cleanup = service._cleanup_tasks[str(request.request_id)]
    await service.stop()

    assert cleanup.cancelled()
    _assert_chat_archives_empty(service, broker)


async def test_user_persistence_failure_emits_terminal_error_without_hanging() -> None:
    store = FailingConversationStore(fail_user=True)
    service = _service_with_store(
        RetrievalResult(hits=[_hit()], max_score=0.9), FakeLLM(["回答"]), store
    )

    events = await asyncio.wait_for(_collect(service, _request()), timeout=0.2)

    assert [event.type for event in events] == ["error"]
    assert events[0].payload["code"] == "CHAT_GENERATION_FAILED"
    assert store.add_attempts == [("user", "completed")]


async def test_completed_assistant_persistence_failure_terminalizes_after_partial_delta() -> None:
    store = FailingConversationStore(fail_assistant_status="completed")
    service = _service_with_store(
        RetrievalResult(hits=[_hit()], max_score=0.9), FakeLLM(["部分回答 [S1]"]), store
    )

    events = await asyncio.wait_for(_collect(service, _request()), timeout=0.2)

    assert [event.type for event in events] == ["citations", "delta", "error"]
    assert events[-1].payload["code"] == "CHAT_GENERATION_FAILED"
    assert store.add_attempts == [
        ("user", "completed"),
        ("assistant", "completed"),
    ]


async def test_provider_error_stays_canonical_when_error_persistence_fails() -> None:
    store = FailingConversationStore(fail_assistant_status="error")
    service = _service_with_store(
        RetrievalResult(hits=[_hit()], max_score=0.9),
        FakeLLM(error=DomainError("MODEL_TIMEOUT", "模型服务响应超时", 504, True)),
        store,
    )

    events = await asyncio.wait_for(_collect(service, _request()), timeout=0.2)

    assert [event.type for event in events] == ["citations", "error"]
    assert events[-1].payload == {
        "code": "MODEL_TIMEOUT",
        "message": "模型服务响应超时",
        "retryable": True,
    }
    assert store.add_attempts == [
        ("user", "completed"),
        ("assistant", "error"),
    ]


async def test_error_publish_failure_is_observable_and_does_not_hang_subscriber() -> None:
    class ErrorPublishFailingBroker(InMemoryEventBroker):
        async def publish(self, job_id, event_type, payload, *, sequence=None):  # type: ignore[no-untyped-def]
            if event_type == "error":
                raise RuntimeError("broker error publish failed")
            return await super().publish(
                job_id, event_type, payload, sequence=sequence
            )

    store = FailingConversationStore(fail_user=True)
    service = ChatService(
        retriever=FakeRetriever(RetrievalResult(hits=[_hit()], max_score=0.9)),
        llm=FakeLLM(["回答"]),
        conversation_store=store,
        event_broker=ErrorPublishFailingBroker(retention=None),
    )
    request = _request()

    events = await asyncio.wait_for(_collect(service, request), timeout=0.2)
    replayed = await asyncio.wait_for(_collect(service, request), timeout=0.2)

    assert [event.type for event in events] == ["error"]
    assert [event.sequence for event in events] == [1]
    assert replayed == events
    assert len(service.task_errors) == 1
    assert str(service.task_errors[0]) == "broker error publish failed"
    await asyncio.sleep(0)
    assert _pending_stream_helpers() == []
    await service.stop()
    _assert_chat_archives_empty(service, service.event_broker)


async def _collect(service: ChatService, request: ChatStreamRequest):  # type: ignore[no-untyped-def]
    return [event async for event in service.stream(request)]


def _pending_stream_helpers(
    *, exclude: set[asyncio.Task[object]] | None = None
) -> list[asyncio.Task[object]]:
    excluded = exclude or set()
    current = asyncio.current_task()
    helpers: list[asyncio.Task[object]] = []
    for task in asyncio.all_tasks():
        if task is current or task in excluded or task.done():
            continue
        coroutine = task.get_coro()
        if (
            task.get_name().startswith("chat-stream:")
            or type(coroutine).__name__ == "async_generator_asend"
            or getattr(coroutine, "__qualname__", "") == "Event.wait"
        ):
            helpers.append(task)
    return helpers


def _assert_chat_archives_empty(
    service: ChatService, broker: InMemoryEventBroker
) -> None:
    assert broker._jobs == {}
    assert service._started_request_ids == set()
    assert service._last_sequences == {}
    assert service._fallback_terminals == {}
    assert service._fallback_ready == {}
    assert service._active_subscribers == {}
    assert service._cleanup_tasks == {}
