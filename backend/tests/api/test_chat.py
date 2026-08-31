from __future__ import annotations

import json
from uuid import uuid4

from app.imports.events import EventEnvelope
from app.storage.models import MessageRecord, RepositoryRecord
from app.storage.repositories import ConversationStore


class StubChatService:
    def __init__(self) -> None:
        self.requests = []

    async def stream(self, request):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        yield EventEnvelope(
            request_id=request.request_id,
            type="delta",
            sequence=1,
            payload={"content": "回答"},
        )
        yield EventEnvelope(
            request_id=request.request_id,
            type="done",
            sequence=2,
            payload={"messageId": "message-1"},
        )


def _seed_chat(client) -> str:  # type: ignore[no-untyped-def]
    database = client.app.state.database
    with database.session() as session:
        session.add_all(
            [
                RepositoryRecord(id="repo-1", name="SwiftUI"),
                RepositoryRecord(id="repo-2", name="Other"),
            ]
        )
    conversation_store = ConversationStore(database)
    chat_session = conversation_store.create_session(["repo-1"])
    client.app.state.conversation_store = conversation_store
    return chat_session.id


def test_chat_routes_require_runtime_token(client) -> None:
    response = client.post(
        "/api/sessions/missing/messages/stream",
        json={"message": "问题", "repositoryIds": ["repo-1"], "requestId": str(uuid4())},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


def test_chat_stream_returns_shared_envelopes_as_sse(client, auth_headers) -> None:
    session_id = _seed_chat(client)
    service = StubChatService()
    client.app.state.chat_service = service
    request_id = uuid4()

    response = client.post(
        f"/api/sessions/{session_id}/messages/stream",
        headers=auth_headers,
        json={
            "message": "@State 是什么？",
            "repositoryIds": ["repo-1"],
            "requestId": str(request_id),
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.count("id: ") == 2
    frames = [frame for frame in response.text.split("\n\n") if frame]
    payloads = [json.loads(frame.split("data: ", 1)[1]) for frame in frames]
    assert [payload["type"] for payload in payloads] == ["delta", "done"]
    assert payloads[0]["request_id"] == str(request_id)
    assert service.requests[0].session_id == session_id


def test_chat_message_list_returns_persisted_citations(client, auth_headers) -> None:
    session_id = _seed_chat(client)
    citation = {
        "sourceId": "S1",
        "chunkId": "chunk-1",
        "documentId": "doc-1",
        "title": "SwiftUI",
        "sectionPath": None,
        "pageNumber": None,
        "excerpt": "状态",
        "sourceUrl": "https://docs.test/state",
    }
    client.app.state.conversation_store.add_message(
        MessageRecord(
            session_id=session_id,
            role="assistant",
            content="回答 [S1]",
            citations_json=json.dumps([citation]),
        )
    )

    response = client.get(f"/api/sessions/{session_id}/messages", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()[0]["content"] == "回答 [S1]"
    assert response.json()[0]["citations"][0]["sourceId"] == "S1"


def test_chat_validation_uses_stable_error_for_blank_unknown_and_out_of_scope(
    client, auth_headers
) -> None:
    session_id = _seed_chat(client)
    cases = [
        (session_id, {"message": "  ", "repositoryIds": ["repo-1"], "requestId": str(uuid4())}),
        ("unknown", {"message": "问题", "repositoryIds": ["repo-1"], "requestId": str(uuid4())}),
        (session_id, {"message": "问题", "repositoryIds": ["repo-2"], "requestId": str(uuid4())}),
        (session_id, {"message": "问题", "repositoryIds": ["missing"], "requestId": str(uuid4())}),
        (session_id, {"message": "问题", "repositoryIds": ["repo-1"], "requestId": "bad"}),
    ]

    for target_session, body in cases:
        response = client.post(
            f"/api/sessions/{target_session}/messages/stream",
            headers=auth_headers,
            json=body,
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "CHAT_INVALID_REQUEST"
