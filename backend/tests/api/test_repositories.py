from __future__ import annotations

from app.remote.fake import FakeRemoteProvider
from tests.conftest import install_remote_provider


def test_repository_list_does_not_probe_an_unconfigured_provider(client, auth_headers) -> None:
    class ExplodingGateway:
        name = "yuque"

        async def list_repositories(self):
            raise AssertionError("an unconfigured provider must not be probed")

    install_remote_provider(client.app, ExplodingGateway(), configured=False)

    response = client.get("/api/repositories", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == []


def test_repository_list_syncs_fake_yuque(client, auth_headers, remote_provider: FakeRemoteProvider) -> None:
    """Catches the missing remote-to-local repository synchronization route."""
    remote_provider.seed_repository("repo-remote", "SwiftUI")

    response = client.get("/api/repositories", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()[0]["name"] == "SwiftUI"
    assert response.json()[0]["remoteId"] == "repo-remote"
    assert response.json()[0]["provider"] == "yuque"


def test_repository_create_validates_name_and_defaults_to_local(
    client, auth_headers, remote_provider: FakeRemoteProvider
) -> None:
    """A knowledge base must not require or touch a remote provider unless explicitly requested."""
    invalid = client.post("/api/repositories", headers=auth_headers, json={"name": "  "})
    created = client.post("/api/repositories", headers=auth_headers, json={"name": "SwiftUI"})

    assert invalid.status_code == 422
    assert created.status_code == 201
    assert created.json()["name"] == "SwiftUI"
    assert created.json()["provider"] is None
    assert created.json()["remoteId"] is None
    assert client.app.state.repository_store.get(created.json()["id"]).remote_id is None
    assert remote_provider.write_calls == []


def test_repository_create_can_explicitly_target_yuque(
    client, auth_headers, remote_provider: FakeRemoteProvider
) -> None:
    created = client.post(
        "/api/repositories",
        headers=auth_headers,
        json={"name": "SwiftUI", "provider": "yuque"},
    )

    assert created.status_code == 201
    assert created.json()["provider"] == "yuque"
    assert created.json()["remoteId"] == "repo-1"
    record = client.app.state.repository_store.get(created.json()["id"])
    assert record.remote_id == "repo-1"
    assert record.provider == "yuque"


def test_repository_create_rejects_unknown_provider(client, auth_headers) -> None:
    response = client.post(
        "/api/repositories",
        headers=auth_headers,
        json={"name": "SwiftUI", "provider": "unknown"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "REMOTE_PROVIDER_UNKNOWN"
