from __future__ import annotations

import json
from uuid import uuid4

from app.core.llm import ChatDelta
from app.storage.models import MessageRecord
from app.storage.repositories import ConversationStore


class _DistillationLLM:
    async def stream_chat(self, request):  # type: ignore[no-untyped-def]
        del request
        yield ChatDelta(content="# Draft\n- point")


def _frames(response) -> list[dict]:  # type: ignore[no-untyped-def]
    return [
        json.loads(frame.split("data: ", 1)[1])
        for frame in response.text.split("\n\n")
        if frame
    ]


def _session(client) -> str:  # type: ignore[no-untyped-def]
    return ConversationStore(client.app.state.database).create_session([str(uuid4())]).id


def test_persisted_citations_restore_their_kind_from_stable_source_prefixes(
    client, auth_headers
) -> None:
    """Catches M#/W# history being decoded as document citations or discarded."""
    session_id = _session(client)
    client.app.state.conversation_store.add_message(
        MessageRecord(
            session_id=session_id,
            role="assistant",
            content="答案 [S1] [M1] [W1]",
            citations_json=json.dumps(
                [
                    {
                        "sourceId": "S1",
                        "chunkId": "chunk-1",
                        "documentId": "document-1",
                        "title": "Document",
                        "excerpt": "document evidence",
                    },
                    {
                        "sourceId": "M1",
                        "memoryKind": "distillation",
                        "memoryId": "memory-1",
                        "title": "Memory",
                        "excerpt": "memory evidence",
                        "sessionId": session_id,
                    },
                    {
                        "sourceId": "W1",
                        "searchRunId": str(uuid4()),
                        "resultId": str(uuid4()),
                        "title": "Web",
                        "excerpt": "web evidence",
                        "sourceUrl": "https://example.test/evidence",
                        "retrievedAt": "2026-09-04T00:00:00Z",
                    },
                ]
            ),
        )
    )

    response = client.get(f"/api/sessions/{session_id}/messages", headers=auth_headers)

    assert response.status_code == 200
    citations = response.json()[0]["citations"]
    assert [citation["kind"] for citation in citations] == ["document", "memory", "web"]
    assert "documentId" not in citations[1]
    assert "chunkId" not in citations[1]


def test_distillation_generation_events_replay_after_last_event_id(client, auth_headers) -> None:
    """Catches distillation SSE publishing only a terminal snapshot."""
    client.app.state.distillation_service.llm = _DistillationLLM()
    session_id = _session(client)

    created = client.post(f"/api/sessions/{session_id}/distillations", headers=auth_headers)
    assert created.status_code == 200
    distillation_id = created.json()["id"]

    first = client.get(f"/api/distillations/{distillation_id}/events", headers=auth_headers)
    replay = client.get(
        f"/api/distillations/{distillation_id}/events",
        headers={**auth_headers, "Last-Event-ID": "1"},
    )

    assert [frame["type"] for frame in _frames(first)] == ["progress", "done"]
    assert [frame["sequence"] for frame in _frames(first)] == [1, 2]
    assert [frame["type"] for frame in _frames(replay)] == ["done"]
    assert [frame["sequence"] for frame in _frames(replay)] == [2]


def test_distillation_save_events_continue_the_durable_sequence(client, auth_headers) -> None:
    """Catches a save lifecycle resetting or omitting its progress event."""
    client.app.state.distillation_service.llm = _DistillationLLM()
    session_id = _session(client)
    created = client.post(f"/api/sessions/{session_id}/distillations", headers=auth_headers)
    assert created.status_code == 200

    saved = client.post(
        f"/api/distillations/{created.json()['id']}/save",
        headers=auth_headers,
        json={"target": "local"},
    )
    replay = client.get(
        f"/api/distillations/{created.json()['id']}/events",
        headers={**auth_headers, "Last-Event-ID": "2"},
    )

    assert saved.status_code == 200
    assert [frame["type"] for frame in _frames(replay)] == ["progress", "done"]
    assert [frame["sequence"] for frame in _frames(replay)] == [3, 4]
