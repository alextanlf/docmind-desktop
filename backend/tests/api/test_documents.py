from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from starlette.requests import Request

from app.api.documents import update_document
from app.api.errors import DomainError
from app.core.embedding import FakeEmbeddingProvider
from app.schemas.documents import DocumentInput
from app.remote.fake import FakeRemoteProvider


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


def _seed_repository(client) -> tuple[str, FakeRemoteProvider]:  # type: ignore[no-untyped-def]
    gateway = client.app.state.remote_registry.get("yuque")
    remote = gateway.seed_repository("repo-remote", "SwiftUI")
    repository = client.app.state.repository_store.upsert_remote(
        provider="yuque",
        remote_id=remote.remote_id,
        name=remote.name,
        description=None,
        remote_url=remote.url,
    )
    client.app.state.embedding_provider = FakeEmbeddingProvider(
        client.app.state.settings.embedding_settings
    )
    return repository.id, gateway


def _vector_store(client) -> RecordingVectorStore:  # type: ignore[no-untyped-def]
    vector_store = RecordingVectorStore()
    client.app.state.vector_store = vector_store
    return vector_store


def test_local_document_crud_never_calls_yuque(client, auth_headers) -> None:
    repository = client.app.state.repository_store.create_local(name="Local knowledge")
    gateway = client.app.state.remote_registry.get("yuque")
    client.app.state.embedding_provider = FakeEmbeddingProvider(
        client.app.state.settings.embedding_settings
    )
    vectors = _vector_store(client)

    created = client.post(
        f"/api/repositories/{repository.id}/documents",
        headers=auth_headers,
        json={"title": "Local note", "content": "# Local note\n\nBody"},
    )
    document_id = created.json()["id"]
    updated = client.put(
        f"/api/documents/{document_id}",
        headers=auth_headers,
        json={"title": "Updated note", "content": "# Updated note\n\nNew body"},
    )
    deleted = client.request(
        "DELETE",
        f"/api/documents/{document_id}",
        headers=auth_headers,
        json={"confirm": True},
    )

    assert created.status_code == 201
    assert created.json()["remoteId"] is None
    assert updated.status_code == 200
    assert updated.json()["title"] == "Updated note"
    assert deleted.status_code == 204
    assert client.app.state.document_store.get(document_id) is None
    assert vectors.ids == set()
    assert gateway.write_calls == []


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
    assert repositories.json()[0]["indexedDocumentCount"] == 1
    deleted = client.request(
        "DELETE", f"/api/documents/{document_id}", headers=auth_headers, json={"confirm": True}
    )
    assert deleted.status_code == 204
    assert client.get("/api/repositories", headers=auth_headers).json()[0]["documentCount"] == 0
    assert client.get("/api/repositories", headers=auth_headers).json()[0]["indexedDocumentCount"] == 0


def test_document_create_preserves_submitted_markdown_whitespace(client, auth_headers) -> None:
    repository_id, _ = _seed_repository(client)
    content = "\n# State\n\n状态管理  \n"

    created = client.post(
        f"/api/repositories/{repository_id}/documents",
        headers=auth_headers,
        json={"title": "State", "content": content},
    )

    assert created.status_code == 201
    assert created.json()["content"] == content


def test_document_create_removes_the_recovery_marker_from_remote_content(
    client, auth_headers
) -> None:
    repository_id, gateway = _seed_repository(client)
    content = "# State\n\n状态管理"

    created = client.post(
        f"/api/repositories/{repository_id}/documents",
        headers=auth_headers,
        json={"title": "State", "content": content},
    )

    assert created.status_code == 201
    remote = asyncio.run(gateway.read_document("doc-1"))
    assert remote.content == content
    assert "docmind-mutation:" not in remote.content


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

    async def fail_delete(document_id: str, repository_id: str) -> None:
        raise DomainError("YUQUE_PAGE_CHANGED", "remote unavailable", 503, True)

    gateway.delete_document = fail_delete  # type: ignore[method-assign]
    response = client.request(
        "DELETE", f"/api/documents/{document_id}", headers=auth_headers, json={"confirm": True}
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "REMOTE_OPERATION_FAILED"
    assert client.app.state.import_service.document_store.get(document_id) is not None


@pytest.mark.parametrize(
    ("code", "status_code", "message"),
    [
        # 语雀：曾经被通用层硬编码白名单覆盖。
        ("YUQUE_LOGIN_REQUIRED", 401, "语雀登录已失效，请重新登录"),
        # 飞书：曾因不在白名单里而被降级成 503「重试远程操作」，而重试必然再次失败。
        ("FEISHU_LOGIN_REQUIRED", 401, "飞书登录已失效，请重新授权"),
        # 未登记过的 provider：只要声明 auth_expired 就能自动获得透传行为。
        ("ACME_LOGIN_REQUIRED", 401, "ACME 登录已失效，请重新登录"),
    ],
)
def test_auth_expired_errors_pass_through_without_provider_coupling(
    client, auth_headers, code: str, status_code: int, message: str
) -> None:
    """Catches the generic layer recognising provider error codes.

    The mapper must branch on the ``auth_expired`` flag instead of an
    enumeration of platform codes, so any provider's login failure reaches the
    user as-is. Hard-coding a third code here would have passed under the old
    implementation only for that code; adding a provider used to silently
    downgrade to ``REMOTE_OPERATION_FAILED``.
    """
    repository_id, gateway = _seed_repository(client)
    created = client.post(
        f"/api/repositories/{repository_id}/documents",
        headers=auth_headers,
        json={"title": "Auth", "content": "# Auth\n\n凭证失效"},
    )
    document_id = created.json()["id"]

    async def require_login(document_id: str, repository_id: str) -> None:
        raise DomainError(code, message, status_code, False, "重新登录", auth_expired=True)

    gateway.delete_document = require_login  # type: ignore[method-assign]
    response = client.request(
        "DELETE", f"/api/documents/{document_id}", headers=auth_headers, json={"confirm": True}
    )

    body = response.json()["error"]
    assert response.status_code == status_code
    assert body["code"] == code
    assert body["message"] == message
    assert body["retryable"] is False


def test_generic_remote_error_is_normalised_without_auth_expired(client, auth_headers) -> None:
    """Catches loss of the fallback path: non-auth failures still normalise."""
    repository_id, gateway = _seed_repository(client)
    created = client.post(
        f"/api/repositories/{repository_id}/documents",
        headers=auth_headers,
        json={"title": "State", "content": "# State\n\n状态管理"},
    )
    document_id = created.json()["id"]

    async def fail_delete(document_id: str, repository_id: str) -> None:
        # Same message shape as an auth error but without the flag.
        raise DomainError("ACME_LOGIN_REQUIRED", "看起来像登录失效", 503, True)

    gateway.delete_document = fail_delete  # type: ignore[method-assign]
    response = client.request(
        "DELETE", f"/api/documents/{document_id}", headers=auth_headers, json={"confirm": True}
    )

    assert response.json()["error"]["code"] == "REMOTE_OPERATION_FAILED"


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
    pending_vector_ids = set(vector_store.ids)
    unconfirmed = client.request(
        "DELETE", f"/api/documents/{document_id}", headers=auth_headers, json={}
    )
    pending_after_unconfirmed = client.app.state.vector_cleanup_store.list()
    vectors_after_unconfirmed = set(vector_store.ids)
    second = client.request(
        "DELETE", f"/api/documents/{document_id}", headers=auth_headers, json={"confirm": True}
    )

    assert first.status_code == 204
    assert unconfirmed.status_code == 400
    assert unconfirmed.json()["error"]["code"] == "CONFIRMATION_REQUIRED"
    assert vectors_after_unconfirmed == pending_vector_ids
    assert len(pending_after_unconfirmed) == 1
    assert set(json.loads(pending_after_unconfirmed[0].vector_ids_json)) == pending_vector_ids
    assert second.status_code == 204
    assert vector_store.ids == set()
    assert client.app.state.document_store.get(document_id) is None
    assert document_id not in {item.remote_id for item in asyncio.run(gateway.list_documents("repo-remote"))}


def test_document_create_response_contains_submitted_markdown(client, auth_headers) -> None:
    """Catches returning a detached document before its Markdown path is persisted."""
    repository_id, _ = _seed_repository(client)
    response = client.post(f"/api/repositories/{repository_id}/documents", headers=auth_headers, json={"title": "State", "content": "# State\n\n内容"})
    assert response.status_code == 201
    assert response.json()["content"] == "# State\n\n内容"


def test_shared_vector_ids_are_not_deleted_when_index_commit_raises(client, auth_headers, monkeypatch) -> None:
    """A replacement that reuses an old vector must not delete that shared ID on rollback."""
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
        json={"title": "State renamed", "content": "# State\n\n状态管理"},
    )
    monkeypatch.setattr(client.app.state.document_store, "replace_chunks", original_replace)

    assert failed.status_code == 503
    assert vector_store.ids == previous_ids
    assert client.app.state.document_store.vector_ids(document_id) == list(previous_ids)


def test_domain_error_after_vector_upsert_rolls_back_remote_and_local_state(client, auth_headers) -> None:
    """DomainError from a vector driver after writing must still trigger compensation."""
    repository_id, gateway = _seed_repository(client)
    vector_store = _vector_store(client)
    created = client.post(
        f"/api/repositories/{repository_id}/documents",
        headers=auth_headers,
        json={"title": "State", "content": "# State\n\n状态管理"},
    )
    document_id = created.json()["id"]
    previous_ids = set(vector_store.ids)
    original_upsert = vector_store.upsert

    def upsert_then_domain_error(repository, ids, texts, embeddings, metadatas):  # type: ignore[no-untyped-def]
        original_upsert(repository, ids, texts, embeddings, metadatas)
        raise DomainError("INDEX_FAILED", "向量写入中断", 503, True)

    vector_store.upsert = upsert_then_domain_error  # type: ignore[method-assign]
    failed = client.put(
        f"/api/documents/{document_id}",
        headers=auth_headers,
        json={"title": "Changed", "content": "# Changed\n\n新内容"},
    )

    assert failed.status_code == 503
    assert vector_store.ids == previous_ids
    assert client.get(f"/api/documents/{document_id}", headers=auth_headers).json()["content"] == "# State\n\n状态管理"
    remote = asyncio.run(gateway.read_document("doc-1"))
    assert remote.content == "# State\n\n状态管理"


def test_cancelled_vector_commit_restores_remote_local_and_file_state(client, auth_headers) -> None:
    """Cancellation during vector commit must be compensated before propagating."""
    repository_id, gateway = _seed_repository(client)
    vector_store = _vector_store(client)
    created = client.post(
        f"/api/repositories/{repository_id}/documents",
        headers=auth_headers,
        json={"title": "State", "content": "# State\n\n状态管理"},
    )
    document_id = created.json()["id"]
    previous_ids = set(vector_store.ids)
    original_upsert = vector_store.upsert
    cancelled = False

    def upsert_then_cancel(repository, ids, texts, embeddings, metadatas):  # type: ignore[no-untyped-def]
        nonlocal cancelled
        original_upsert(repository, ids, texts, embeddings, metadatas)
        if not cancelled:
            cancelled = True
            raise asyncio.CancelledError()

    vector_store.upsert = upsert_then_cancel  # type: ignore[method-assign]
    scope = {"type": "http", "app": client.app}
    request = Request(scope)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            update_document(
                request,
                document_id,
                DocumentInput(title="Changed", content="# Changed\n\n新内容"),
            )
        )

    assert vector_store.ids == previous_ids
    assert client.get(f"/api/documents/{document_id}", headers=auth_headers).json()["content"] == "# State\n\n状态管理"
    remote = asyncio.run(gateway.read_document("doc-1"))
    assert remote.content == "# State\n\n状态管理"


def test_update_file_replace_failure_restores_exact_previous_state(client, auth_headers, monkeypatch) -> None:
    """A failed staged Markdown replace must roll back index, metadata, and remote content."""
    repository_id, gateway = _seed_repository(client)
    vector_store = _vector_store(client)
    created = client.post(
        f"/api/repositories/{repository_id}/documents",
        headers=auth_headers,
        json={"title": "State", "content": "# State\n\n状态管理"},
    )
    document_id = created.json()["id"]
    previous_ids = set(vector_store.ids)
    document = client.app.state.document_store.get(document_id)
    old_path = Path(document.markdown_path)

    def fail_replace(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise OSError("replace failed")

    monkeypatch.setattr("app.api.documents.os.replace", fail_replace)
    failed = client.put(
        f"/api/documents/{document_id}",
        headers=auth_headers,
        json={"title": "Changed", "content": "# Changed\n\n新内容"},
    )

    assert failed.status_code == 503
    assert old_path.read_text(encoding="utf-8") == "# State\n\n状态管理"
    restored = client.app.state.document_store.get(document_id)
    assert restored.title == "State"
    assert client.app.state.document_store.vector_ids(document_id) == list(previous_ids)
    assert vector_store.ids == previous_ids
    assert asyncio.run(gateway.read_document("doc-1")).content == "# State\n\n状态管理"


def test_update_editor_failure_restores_exact_previous_state(client, auth_headers, monkeypatch) -> None:
    """A DB metadata failure after file replacement must restore every previous surface."""
    repository_id, gateway = _seed_repository(client)
    vector_store = _vector_store(client)
    created = client.post(
        f"/api/repositories/{repository_id}/documents",
        headers=auth_headers,
        json={"title": "State", "content": "# State\n\n状态管理"},
    )
    document_id = created.json()["id"]
    previous_ids = set(vector_store.ids)
    document = client.app.state.document_store.get(document_id)
    old_path = Path(document.markdown_path)
    original_update_editor = client.app.state.document_store.update_editor

    def fail_update_editor(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("editor metadata failed")

    client.app.state.document_store.update_editor = fail_update_editor  # type: ignore[method-assign]
    try:
        failed = client.put(
            f"/api/documents/{document_id}",
            headers=auth_headers,
            json={"title": "Changed", "content": "# Changed\n\n新内容"},
        )
    finally:
        client.app.state.document_store.update_editor = original_update_editor  # type: ignore[method-assign]

    assert failed.status_code == 503
    assert old_path.read_text(encoding="utf-8") == "# State\n\n状态管理"
    restored = client.app.state.document_store.get(document_id)
    assert restored.title == "State"
    assert client.app.state.document_store.vector_ids(document_id) == list(previous_ids)
    assert vector_store.ids == previous_ids
    assert asyncio.run(gateway.read_document("doc-1")).content == "# State\n\n状态管理"


def test_remote_rollback_failure_is_retried_during_app_recreation(client, auth_headers, tmp_path) -> None:
    """Failed remote compensation is durable and retried when a new app starts."""
    repository_id, gateway = _seed_repository(client)
    vector_store = _vector_store(client)
    created = client.post(
        f"/api/repositories/{repository_id}/documents",
        headers=auth_headers,
        json={"title": "State", "content": "# State\n\n状态管理"},
    )
    document_id = created.json()["id"]
    original_update = gateway.update_document
    calls = 0
    allow_rollback = False

    async def flaky_update(request):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls == 2 and not allow_rollback:
            raise RuntimeError("remote rollback unavailable")
        return await original_update(request)

    gateway.update_document = flaky_update  # type: ignore[method-assign]
    vector_store.fail_upsert = True
    failed = client.put(
        f"/api/documents/{document_id}",
        headers=auth_headers,
        json={"title": "Changed", "content": "# Changed\n\n新内容"},
    )
    assert failed.status_code == 503
    assert asyncio.run(gateway.read_document("doc-1")).content == "# Changed\n\n新内容"

    allow_rollback = True
    from fastapi.testclient import TestClient

    from app.config import AppSettings
    from app.core.secrets import MemorySecretStore
    from app.main import create_app

    settings = AppSettings(
        session_token=client.app.state.settings.session_token,
        data_dir=client.app.state.settings.data_dir,
        environment="test",
    )
    with TestClient(
        create_app(
            settings,
            secret_store=MemorySecretStore(),
            providers={"yuque": gateway},
            embedding_provider=FakeEmbeddingProvider(settings.embedding_settings),
        )
    ):
        pass

    assert asyncio.run(gateway.read_document("doc-1")).content == "# State\n\n状态管理"
