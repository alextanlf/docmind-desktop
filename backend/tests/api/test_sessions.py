from __future__ import annotations

from app.storage.models import RepositoryRecord


def test_session_create_and_message_history(client, auth_headers) -> None:
    """Catches missing session creation and empty persisted-history contracts."""
    created = client.post("/api/sessions", json={"repositoryIds": []}, headers=auth_headers)
    session_id = created.json()["id"]
    history = client.get(f"/api/sessions/{session_id}/messages", headers=auth_headers)

    assert created.status_code == 201
    assert created.json()["title"] == "新会话"
    assert history.status_code == 200
    assert history.json() == []


def test_sessions_validate_repository_scope_and_unknown_ids(client, auth_headers) -> None:
    """Catches sessions scoped to missing repositories and missing session histories."""
    with client.app.state.database.session() as database_session:
        database_session.add(RepositoryRecord(id="repo-1", name="SwiftUI"))

    invalid = client.post(
        "/api/sessions", headers=auth_headers, json={"repositoryIds": ["missing"]}
    )
    created = client.post(
        "/api/sessions", headers=auth_headers, json={"repositoryIds": ["repo-1"]}
    )
    missing = client.get("/api/sessions/missing/messages", headers=auth_headers)

    assert invalid.status_code == 404
    assert invalid.json()["error"]["code"] == "NOT_FOUND"
    assert created.status_code == 201
    assert created.json()["repositoryIds"] == ["repo-1"]
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"
