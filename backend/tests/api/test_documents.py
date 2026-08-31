from __future__ import annotations

from app.api.errors import DomainError
from app.core.embedding import FakeEmbeddingProvider
from app.yuque.gateway import FakeYuqueGateway


def _seed_repository(client) -> tuple[str, FakeYuqueGateway]:  # type: ignore[no-untyped-def]
    gateway = FakeYuqueGateway()
    remote = gateway.seed_repository("repo-remote", "SwiftUI")
    repository = client.app.state.repository_store.upsert_remote(
        yuque_id=remote.yuque_id, name=remote.name, description=None, yuque_url=remote.url
    )
    client.app.state.yuque_gateway = gateway
    client.app.state.embedding_provider = FakeEmbeddingProvider(
        client.app.state.settings.embedding_settings
    )
    return repository.id, gateway


def test_document_create_read_and_update_keep_local_index_in_sync(client, auth_headers) -> None:
    """Catches Markdown mutations that do not persist readable content and chunks."""
    repository_id, _ = _seed_repository(client)

    created = client.post(
        f"/api/repositories/{repository_id}/documents",
        headers=auth_headers,
        json={"title": "State", "content": "# State\n\n状态管理"},
    )
    assert created.status_code == 201
    document_id = created.json()["id"]
    read = client.get(f"/api/documents/{document_id}", headers=auth_headers)
    updated = client.put(
        f"/api/documents/{document_id}",
        headers=auth_headers,
        json={"title": "State updated", "content": "# State updated\n\n新的状态管理"},
    )

    assert read.json()["content"] == "# State\n\n状态管理"
    assert updated.status_code == 200
    assert updated.json()["title"] == "State updated"
    assert updated.json()["chunkCount"] == 1
    assert client.app.state.import_service.document_store.vector_ids(document_id)


def test_delete_document_requires_confirmation(client, auth_headers) -> None:
    """Catches destructive local and remote deletion without explicit confirmation."""
    repository_id, _ = _seed_repository(client)
    created = client.post(
        f"/api/repositories/{repository_id}/documents",
        headers=auth_headers,
        json={"title": "State", "content": "# State\n\n状态管理"},
    )
    document_id = created.json()["id"]

    response = client.request("DELETE", f"/api/documents/{document_id}", headers=auth_headers, json={})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "CONFIRMATION_REQUIRED"
    assert client.app.state.import_service.document_store.get(document_id) is not None


def test_confirmed_delete_keeps_local_state_when_remote_delete_fails(client, auth_headers) -> None:
    """Catches removing chunks or records before an unsuccessful remote deletion."""
    repository_id, gateway = _seed_repository(client)
    created = client.post(
        f"/api/repositories/{repository_id}/documents",
        headers=auth_headers,
        json={"title": "State", "content": "# State\n\n状态管理"},
    )
    document_id = created.json()["id"]

    async def fail_delete(_: str) -> None:
        raise DomainError("YUQUE_PAGE_CHANGED", "remote unavailable", 503, True)

    gateway.delete_document = fail_delete  # type: ignore[method-assign]
    response = client.request(
        "DELETE", f"/api/documents/{document_id}", headers=auth_headers, json={"confirm": True}
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "YUQUE_OPERATION_FAILED"
    assert client.app.state.import_service.document_store.get(document_id) is not None
