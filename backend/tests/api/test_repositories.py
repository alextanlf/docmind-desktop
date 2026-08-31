from __future__ import annotations

from app.yuque.gateway import FakeYuqueGateway


def test_repository_list_syncs_fake_yuque(client, auth_headers) -> None:
    """Catches the missing remote-to-local repository synchronization route."""
    fake_yuque = FakeYuqueGateway()
    fake_yuque.seed_repository("repo-remote", "SwiftUI")
    client.app.state.yuque_gateway = fake_yuque

    response = client.get("/api/repositories", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()[0]["name"] == "SwiftUI"
    assert response.json()[0]["yuqueId"] == "repo-remote"


def test_repository_create_validates_name_and_persists_remote_result(client, auth_headers) -> None:
    """Catches accepting blank/oversized names or not persisting gateway creation."""
    client.app.state.yuque_gateway = FakeYuqueGateway()

    invalid = client.post("/api/repositories", headers=auth_headers, json={"name": "  "})
    created = client.post("/api/repositories", headers=auth_headers, json={"name": "SwiftUI"})

    assert invalid.status_code == 422
    assert created.status_code == 201
    assert created.json()["name"] == "SwiftUI"
    assert client.app.state.repository_store.get(created.json()["id"]).yuque_id == "repo-1"
