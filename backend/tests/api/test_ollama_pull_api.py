def test_pull_create_get_and_cancel(client, auth_headers):
    created = client.post("/api/ollama/models/pull", headers=auth_headers, json={"modelName":"qwen2.5:7b"})
    assert created.status_code == 200
    pull_id = created.json()["id"]
    fetched = client.get(f"/api/ollama/models/pull/{pull_id}", headers=auth_headers)
    assert fetched.status_code == 200 and fetched.json()["state"] == "queued"
    cancelled = client.post(f"/api/ollama/models/pull/{pull_id}/cancel", headers=auth_headers)
    assert cancelled.status_code == 200 and cancelled.json()["state"] == "cancelled"
    events = client.get(f"/api/ollama/models/pull/{pull_id}/events", headers={**auth_headers, "Last-Event-ID":"0"})
    assert events.status_code == 200
    assert "event:" in events.text and pull_id in events.text
    assert "OLLAMA_PULL_CANCELLED" in events.text

def test_pull_rejects_invalid_model_tag(client, auth_headers):
    response = client.post("/api/ollama/models/pull", headers=auth_headers, json={"modelName": "bad\nname"})
    assert response.status_code == 422


def test_pull_events_reject_invalid_last_event_id(client, auth_headers):
    response = client.get(
        "/api/ollama/models/pull/00000000-0000-4000-8000-000000000001/events",
        headers={**auth_headers, "Last-Event-ID": "not-a-number"},
    )
    assert response.status_code == 422


def test_pull_events_use_terminal_event_name_and_snapshot_priority(client, auth_headers):
    created = client.post(
        "/api/ollama/models/pull",
        headers=auth_headers,
        json={"modelName": "qwen2.5:7b"},
    )
    pull_id = created.json()["id"]
    client.post(f"/api/ollama/models/pull/{pull_id}/cancel", headers=auth_headers)
    response = client.get(
        f"/api/ollama/models/pull/{pull_id}/events",
        headers={**auth_headers, "Last-Event-ID": "999"},
    )
    assert response.status_code == 200
    assert "event: error" in response.text
    assert '"code":"OLLAMA_PULL_CANCELLED"' in response.text
