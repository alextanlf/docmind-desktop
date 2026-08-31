from __future__ import annotations

import asyncio

from app.api.errors import DomainError
from app.core.embedding import FakeEmbeddingProvider
from app.yuque.gateway import FakeYuqueGateway


class RecordingVectorStore:
    def __init__(self) -> None:
        self.ids: set[str] = set()
        self.fail_upsert = False
        self.fail_delete = False
        self.fail_delete_once = False

    def upsert(self, repository_id, ids, texts, embeddings, metadatas):  # type: ignore[no-untyped-def]
        del repository_id, texts, embeddings, metadatas
        if self.fail_upsert:
            raise RuntimeError("upsert failed")
        self.ids.update(ids)

    def delete(self, repository_id, ids):  # type: ignore[no-untyped-def]
        del repository_id
        if self.fail_delete or self.fail_delete_once:
            self.fail_delete_once = False
            raise RuntimeError("delete failed")
        self.ids.difference_update(ids)


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


def _vector_store(client) -> RecordingVectorStore:  # type: ignore[no-untyped-def]
    vector_store = RecordingVectorStore()
    client.app.state.vector_store = vector_store
    return vector_store


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
    repositories = client.get("/api/repositories", headers=auth_headers)
    assert repositories.json()[0]["documentCount"] == 1
    deleted = client.request(
        "DELETE", f"/api/documents/{document_id}", headers=auth_headers, json={"confirm": True}
    )
    assert deleted.status_code == 204
    assert client.get("/api/repositories", headers=auth_headers).json()[0]["documentCount"] == 0


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


def test_index_upsert_failure_compensates_vectors_and_leaves_old_index(client, auth_headers) -> None:
    """Catches orphan vectors when vector upsert fails after document persistence."""
    repository_id, _ = _seed_repository(client)
    vector_store = _vector_store(client)
    created = client.post(
        f"/api/repositories/{repository_id}/documents",
        headers=auth_headers,
        json={"title": "State", "content": "# State\n\n状态管理"},
    )
    assert created.status_code == 201
    document_id = created.json()["id"]
    previous_ids = set(vector_store.ids)
    vector_store.fail_upsert = True

    failed = client.put(
        f"/api/documents/{document_id}",
        headers=auth_headers,
        json={"title": "Changed", "content": "# Changed\n\n新内容"},
    )

    assert failed.status_code == 503
    assert vector_store.ids == previous_ids
    assert client.get(f"/api/documents/{document_id}", headers=auth_headers).json()["content"] == "# State\n\n状态管理"


def test_index_sqlite_failure_compensates_new_vectors(client, auth_headers, monkeypatch) -> None:
    """Catches orphan vectors when SQLite chunk replacement fails after upsert."""
    repository_id, _ = _seed_repository(client)
    vector_store = _vector_store(client)
    created = client.post(
        f"/api/repositories/{repository_id}/documents",
        headers=auth_headers,
        json={"title": "State", "content": "# State\n\n状态管理"},
    )
    document_id = created.json()["id"]
    previous_ids = set(vector_store.ids)
    original_replace = client.app.state.document_store.replace_chunks

    def fail_replace(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("sqlite failed")

    monkeypatch.setattr(client.app.state.document_store, "replace_chunks", fail_replace)
    failed = client.put(
        f"/api/documents/{document_id}",
        headers=auth_headers,
        json={"title": "Changed", "content": "# Changed\n\n新内容"},
    )
    monkeypatch.setattr(client.app.state.document_store, "replace_chunks", original_replace)

    assert failed.status_code == 503
    assert vector_store.ids == previous_ids
    assert client.app.state.document_store.vector_ids(document_id) == list(previous_ids)


def test_index_stale_vector_delete_failure_does_not_leave_obsolete_vectors(client, auth_headers) -> None:
    """Catches obsolete vectors remaining searchable when a replacement shrinks chunks."""
    repository_id, _ = _seed_repository(client)
    vector_store = _vector_store(client)
    created = client.post(
        f"/api/repositories/{repository_id}/documents",
        headers=auth_headers,
        json={"title": "State", "content": "# State\n\n状态管理"},
    )
    document_id = created.json()["id"]
    vector_store.fail_delete_once = True
    failed = client.put(
        f"/api/documents/{document_id}",
        headers=auth_headers,
        json={"title": "State", "content": "# State"},
    )
    assert failed.status_code == 503
    assert vector_store.ids == set(client.app.state.document_store.vector_ids(document_id))


def test_delete_vector_failure_still_removes_local_and_retry_cleans_pending(client, auth_headers) -> None:
    """Catches remote-success deletes getting stuck on a transient vector-store failure."""
    repository_id, gateway = _seed_repository(client)
    vector_store = _vector_store(client)
    created = client.post(
        f"/api/repositories/{repository_id}/documents",
        headers=auth_headers,
        json={"title": "State", "content": "# State\n\n状态管理"},
    )
    document_id = created.json()["id"]
    vector_store.fail_delete_once = True

    first = client.request(
        "DELETE", f"/api/documents/{document_id}", headers=auth_headers, json={"confirm": True}
    )
    second = client.request(
        "DELETE", f"/api/documents/{document_id}", headers=auth_headers, json={"confirm": True}
    )

    assert first.status_code == 204
    assert second.status_code == 204
    assert client.app.state.document_store.get(document_id) is None
    assert document_id not in {item.yuque_id for item in asyncio.run(gateway.list_documents("repo-remote"))}


def test_document_create_response_contains_submitted_markdown(client, auth_headers) -> None:
    """Catches returning a detached document before its Markdown path is persisted."""
    repository_id, _ = _seed_repository(client)
    response = client.post(f"/api/repositories/{repository_id}/documents", headers=auth_headers, json={"title": "State", "content": "# State\n\n内容"})
    assert response.status_code == 201
    assert response.json()["content"] == "# State\n\n内容"
