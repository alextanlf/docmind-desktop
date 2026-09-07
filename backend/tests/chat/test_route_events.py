from uuid import uuid4

import pytest

from app.api.errors import DomainError
from app.chat.service import ChatService
from app.core.llm import ChatDelta
from app.core.model_router import RoutedStream
from app.imports.events import InMemoryEventBroker
from app.schemas.chat import ChatStreamRequest
from app.schemas.ollama import GenerationRoute
from app.schemas.retrieval import RetrievalHit, RetrievalResult


class Retriever:
    async def search(self, query, repository_ids, top_k=5):
        return RetrievalResult(
            hits=[
                RetrievalHit(
                    chunk_id="c1",
                    document_id="d1",
                    document_title="Doc",
                    text="evidence",
                    section_path="Intro",
                    page_number=None,
                    source_url=None,
                    vector_score=0.9,
                    keyword_score=1.0,
                    fused_score=0.9,
                )
            ],
            max_score=0.9,
        )


class Store:
    def __init__(self):
        self.messages = []
        self.requests = {}

    def add_message(self, message):
        self.messages.append(message)
        return message

    def list_messages(self, session_id):
        return [item for item in self.messages if item.session_id == session_id]

    def claim_chat_request(self, request_id, session_id):
        if request_id in self.requests:
            return self.requests[request_id], False
        record = type("Request", (), {"terminal_type": None, "terminal_payload_json": None})()
        self.requests[request_id] = record
        return record, True

    def complete_chat_request(self, request_id, terminal_type, payload):
        record = self.requests[request_id]
        record.terminal_type = terminal_type
        return record


class RoutedProvider:
    def __init__(self, error=False):
        self.error = error
        self.calls = 0

    async def open_stream(self, request):
        self.calls += 1
        route = GenerationRoute(
            source="cloud", model="deepseek-chat", mode="automatic", fallback_reason="OLLAMA_UNAVAILABLE"
        )

        async def deltas():
            if self.error:
                raise DomainError("ROUTING_CLOUD_UNAVAILABLE", "cloud unavailable", 503, True)
            yield ChatDelta(content="answer [S1]")

        return RoutedStream(route=route, deltas=deltas())


def request():
    return ChatStreamRequest(
        request_id=uuid4(), session_id="s1", message="question", repository_ids=["r1"]
    )


@pytest.mark.asyncio
async def test_chat_route_is_copied_to_progress_and_done_events():
    service = ChatService(
        retriever=Retriever(),
        llm=RoutedProvider(),
        conversation_store=Store(),
        event_broker=InMemoryEventBroker(retention=None),
    )
    events = [event async for event in service.stream(request())]
    route_events = [event for event in events if "route" in event.payload]
    assert [event.type for event in route_events] == ["progress", "done"]
    assert route_events[0].payload["route"] == route_events[-1].payload["route"]


@pytest.mark.asyncio
async def test_chat_route_is_copied_to_error_event():
    service = ChatService(
        retriever=Retriever(),
        llm=RoutedProvider(error=True),
        conversation_store=Store(),
        event_broker=InMemoryEventBroker(retention=None),
    )
    events = [event async for event in service.stream(request())]
    assert events[-1].type == "error"
    assert events[-1].payload["route"]["fallbackReason"] == "OLLAMA_UNAVAILABLE"
