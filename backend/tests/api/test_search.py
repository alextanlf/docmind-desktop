from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from app.search.service import SearchRunView
from app.storage.models import MessageRecord
from app.storage.repositories import ConversationStore


@dataclass
class StubSearchService:
    calls: list[tuple[object, str]]

    async def run(self, request, *, authorization_mode: str = "auto") -> SearchRunView:  # type: ignore[no-untyped-def]
        self.calls.append((request, authorization_mode))
        return SearchRunView(id=uuid4(), status="completed", results=[])


def _seed_message(client, *, role: str) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    store = ConversationStore(client.app.state.database)
    session = store.create_session([])
    message = store.add_message(
        MessageRecord(session_id=session.id, role=role, content="search terms")
    )
    client.app.state.conversation_store = store
    return session.id, message.id


def test_create_search_run_requires_user_message(client, auth_headers) -> None:  # type: ignore[no-untyped-def]
    session_id, message_id = _seed_message(client, role="assistant")
    service = StubSearchService(calls=[])
    client.app.state.search_service = service

    response = client.post(
        "/api/search/runs",
        headers=auth_headers,
        json={
            "requestId": str(uuid4()),
            "sessionId": session_id,
            "userMessageId": message_id,
            "query": "DocMind",
            "authorizationMode": "explicit",
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "SEARCH_INVALID_CONTEXT"
    assert service.calls == []


def test_create_search_run_forwards_authorization_mode(client, auth_headers) -> None:  # type: ignore[no-untyped-def]
    session_id, message_id = _seed_message(client, role="user")
    service = StubSearchService(calls=[])
    client.app.state.search_service = service

    response = client.post(
        "/api/search/runs",
        headers=auth_headers,
        json={
            "requestId": str(uuid4()),
            "sessionId": session_id,
            "userMessageId": message_id,
            "query": "DocMind",
            "authorizationMode": "explicit",
        },
    )

    assert response.status_code == 200
    assert len(service.calls) == 1
    assert service.calls[0][1] == "explicit"
