def test_delete_session_requires_typed_confirmation(client, auth_headers):
    response = client.request("DELETE", "/api/sessions/00000000-0000-0000-0000-000000000001", headers=auth_headers, json={"confirm": False})
    assert response.status_code == 400


def test_ended_session_rejects_chat_before_opening_stream(client, auth_headers):
    from app.storage.repositories import ConversationStore
    session = ConversationStore(client.app.state.database).create_session([])
    ConversationStore(client.app.state.database).end_session(session.id)
    response = client.post(f"/api/sessions/{session.id}/messages/stream", headers=auth_headers, json={"requestId": "00000000-0000-0000-0000-000000000002", "message": "x", "repositoryIds": []})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SESSION_ENDED"
