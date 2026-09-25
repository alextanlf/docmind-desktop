from __future__ import annotations

from app.yuque.gateway import FakeYuqueGateway


def test_repository_list_does_not_probe_an_unbound_web_session(client, auth_headers) -> None:
    class ExplodingGateway:
        async def list_repositories(self):
            raise AssertionError("unbound repository list must not launch Yuque")

    client.app.state.yuque_web_gateway = ExplodingGateway()

    response = client.get("/api/repositories", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == []


def test_repository_list_syncs_fake_yuque(client, auth_headers) -> None:
    """Catches the missing remote-to-local repository synchronization route."""
    fake_yuque = FakeYuqueGateway()
    fake_yuque.seed_repository("repo-remote", "SwiftUI")
    client.app.state.yuque_gateway = fake_yuque

    response = client.get("/api/repositories", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()[0]["name"] == "SwiftUI"
    assert response.json()[0]["yuqueId"] == "repo-remote"


def test_repository_create_validates_name_and_defaults_to_local(client, auth_headers) -> None:
    """A knowledge base must not require or touch Yuque unless explicitly requested."""
    gateway = FakeYuqueGateway()
    client.app.state.yuque_gateway = gateway

    invalid = client.post("/api/repositories", headers=auth_headers, json={"name": "  "})
    created = client.post("/api/repositories", headers=auth_headers, json={"name": "SwiftUI"})

    assert invalid.status_code == 422
    assert created.status_code == 201
    assert created.json()["name"] == "SwiftUI"
    assert created.json()["yuqueId"] is None
    assert client.app.state.repository_store.get(created.json()["id"]).yuque_id is None
    assert gateway.write_calls == []


def test_repository_create_can_explicitly_target_yuque(client, auth_headers) -> None:
    client.app.state.yuque_gateway = FakeYuqueGateway()

    created = client.post(
        "/api/repositories",
        headers=auth_headers,
        json={"name": "SwiftUI", "createRemote": True},
    )

    assert created.status_code == 201
    assert created.json()["yuqueId"] == "repo-1"
    assert client.app.state.repository_store.get(created.json()["id"]).yuque_id == "repo-1"
