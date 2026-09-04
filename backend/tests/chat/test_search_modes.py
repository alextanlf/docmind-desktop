from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.chat.service import ChatService
from app.imports.events import InMemoryEventBroker
from app.schemas.chat import ChatStreamRequest
from app.schemas.retrieval import RetrievalResult
from tests.chat.test_service import FakeConversationStore, FakeLLM, FakeRetriever


class Settings:
    def __init__(self, mode):
        self.mode = mode

    def web_search(self):
        return SimpleNamespace(mode=self.mode, max_results=5)


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
    ("mode", "calls", "suggested"), [("off", 0, False), ("ask", 0, True), ("auto", 1, False)]
)
async def test_low_evidence_respects_search_mode(mode, calls, suggested):
    search = Search()
    store = Store()
    service = ChatService(
        retriever=FakeRetriever(RetrievalResult(hits=[], max_score=0)),
        llm=FakeLLM(["web [W1]"]),
        conversation_store=store,
        event_broker=InMemoryEventBroker(retention=None),
        search_service=search,
        settings_service=Settings(mode),
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
    if mode == "auto":
        assert any(item.get("sourceId") == "W1" for item in events[0].payload["citations"])


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
