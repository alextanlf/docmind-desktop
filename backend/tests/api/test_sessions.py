from __future__ import annotations

from app.storage.models import DocumentChunkRecord, DocumentRecord, RepositoryRecord


def test_session_create_rejects_an_empty_repository_scope(client, auth_headers) -> None:
    """Catches session creation succeeding with no authorized knowledge base."""
    created = client.post("/api/sessions", json={"repositoryIds": []}, headers=auth_headers)

    assert created.status_code == 400
    assert created.json()["error"]["code"] == "SESSION_INVALID_REQUEST"


def test_sessions_require_known_indexed_repository_scope(client, auth_headers) -> None:
    """Catches sessions scoped to missing or not-yet-indexed repositories."""
    with client.app.state.database.session() as database_session:
        database_session.add(RepositoryRecord(id="repo-1", name="SwiftUI"))
    pending = client.post(
        "/api/sessions", headers=auth_headers, json={"repositoryIds": ["repo-1"]}
    )
    with client.app.state.database.session() as database_session:
        database_session.add(DocumentRecord(id="doc-1", repository_id="repo-1", title="State"))
        database_session.add(
            DocumentChunkRecord(
                id="chunk-1", document_id="doc-1", repository_id="repo-1", chunk_index=0,
                text="state", token_count=1,
            )
        )

    invalid = client.post(
        "/api/sessions", headers=auth_headers, json={"repositoryIds": ["missing"]}
    )
    created = client.post(
        "/api/sessions", headers=auth_headers, json={"repositoryIds": ["repo-1"]}
    )
    missing = client.get("/api/sessions/missing/messages", headers=auth_headers)

    assert invalid.status_code == 404
    assert invalid.json()["error"]["code"] == "NOT_FOUND"
    assert pending.status_code == 400
    assert pending.json()["error"]["code"] == "SESSION_INVALID_REQUEST"
    assert created.status_code == 201
    assert created.json()["repositoryIds"] == ["repo-1"]
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"
