from __future__ import annotations

from app.core.embedding import FakeEmbeddingProvider
from app.remote.fake import FakeRemoteProvider
from app.remote.provider import ProviderCapabilities, ProviderIdentity
from tests.conftest import install_remote_provider


class ParentCapableProvider(FakeRemoteProvider):
    def __init__(self) -> None:
        super().__init__(name="feishu", label="飞书文档")
        self.identity = ProviderIdentity(
            name="feishu",
            label="飞书文档",
            capabilities=ProviderCapabilities(
                browser_install=False,
                marker_lookup=True,
                parent_node_write=True,
            ),
        )
        self.created_parent_ids: list[str | None] = []

    async def create_document(self, request):  # type: ignore[no-untyped-def]
        self.created_parent_ids.append(request.parent_id)
        return await super().create_document(request)


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


def test_repository_parent_binding_persists_across_provider_refresh(client, auth_headers) -> None:
    provider = ParentCapableProvider()
    provider.seed_repository("sp1", "飞书知识库")
    install_remote_provider(client.app, provider)

    listed = client.get("/api/repositories", headers=auth_headers).json()[0]
    updated = client.patch(
        f"/api/repositories/{listed['id']}",
        headers=auth_headers,
        json={"remoteParentId": "wikParent"},
    )

    assert updated.status_code == 200
    assert updated.json()["remoteParentId"] == "wikParent"
    assert client.app.state.repository_store.get(listed["id"]).remote_parent_id == "wikParent"
    refreshed = client.get("/api/repositories", headers=auth_headers).json()[0]
    assert refreshed["remoteParentId"] == "wikParent"


def test_remote_document_create_uses_repository_parent(client, auth_headers) -> None:
    provider = ParentCapableProvider()
    provider.seed_repository("sp1", "飞书知识库")
    install_remote_provider(client.app, provider)
    client.app.state.embedding_provider = FakeEmbeddingProvider(
        client.app.state.settings.embedding_settings
    )

    listed = client.get("/api/repositories", headers=auth_headers).json()[0]
    client.patch(
        f"/api/repositories/{listed['id']}",
        headers=auth_headers,
        json={"remoteParentId": "wikParent"},
    )
    response = client.post(
        f"/api/repositories/{listed['id']}/documents",
        headers=auth_headers,
        json={"title": "Child", "content": "# Child"},
    )

    assert response.status_code == 201
    assert provider.created_parent_ids == ["wikParent"]


def test_repository_parent_binding_requires_provider_capability(client, auth_headers) -> None:
    provider = FakeRemoteProvider()
    provider.seed_repository("repo-remote", "SwiftUI")
    install_remote_provider(client.app, provider)
    listed = client.get("/api/repositories", headers=auth_headers).json()[0]

    response = client.patch(
        f"/api/repositories/{listed['id']}",
        headers=auth_headers,
        json={"remoteParentId": "unsupported"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "REMOTE_CAPABILITY_UNSUPPORTED"


def test_repository_parent_binding_rejects_local_repository(client, auth_headers) -> None:
    created = client.post("/api/repositories", headers=auth_headers, json={"name": "Local"})

    response = client.patch(
        f"/api/repositories/{created.json()['id']}",
        headers=auth_headers,
        json={"remoteParentId": "wikParent"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REMOTE_NOT_BOUND"


def test_repository_create_rejects_unknown_provider(client, auth_headers) -> None:
    response = client.post(
        "/api/repositories",
        headers=auth_headers,
        json={"name": "SwiftUI", "provider": "unknown"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "REMOTE_PROVIDER_UNKNOWN"
