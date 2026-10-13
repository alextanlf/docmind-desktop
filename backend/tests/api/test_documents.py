from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

import pymupdf
import pytest
from starlette.requests import Request

from app.api.documents import update_document
from app.api.errors import DomainError
from app.core.embedding import FakeEmbeddingProvider
from app.document.path_tokens import FILE_DOMAIN, path_token, path_token_matches
from app.remote.fake import FakeRemoteProvider
from app.schemas.documents import DocumentInput
from app.schemas.graph import GraphEdge, GraphNode
from app.storage.models import DocumentChunkRecord, DocumentRecord
from tests.conftest import RUNTIME_TOKEN


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
    """两个删除入口都必须显式确认，缺 confirm 时什么都不许动。"""
    repository_id, _ = _seed_repository(client)
    created = client.post(
        f"/api/repositories/{repository_id}/documents",
        headers=auth_headers,
        json={"title": "State", "content": "# State\n\n状态管理"},
    )
    document_id = created.json()["id"]

    response = client.request("DELETE", f"/api/documents/{document_id}", headers=auth_headers, json={})
    remote_response = client.request(
        "DELETE", f"/api/documents/{document_id}/remote", headers=auth_headers, json={}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "CONFIRMATION_REQUIRED"
    assert remote_response.status_code == 400
    assert remote_response.json()["error"]["code"] == "CONFIRMATION_REQUIRED"
    assert client.app.state.import_service.document_store.get(document_id) is not None


def test_default_delete_never_touches_the_remote(client, auth_headers) -> None:
    """默认的删除只清本地。远端原文是用户的资料，删除不该顺手带走它。

    这条守的是一个真实事故面：从前 DELETE /api/documents/{id} 对远端文档会调
    provider.delete_document，而确认框只写「将从「X」删除「Y」」——用户以为自己
    在本地清理，实际删掉了语雀/飞书上的原文。
    """
    repository_id, gateway = _seed_repository(client)
    created = client.post(
        f"/api/repositories/{repository_id}/documents",
        headers=auth_headers,
        json={"title": "State", "content": "# State\n\n状态管理"},
    )
    document_id = created.json()["id"]
    remote_id = client.app.state.import_service.document_store.get(document_id).remote_id
    assert remote_id

    response = client.request(
        "DELETE", f"/api/documents/{document_id}", headers=auth_headers, json={"confirm": True}
    )

    assert response.status_code == 204
    assert client.app.state.import_service.document_store.get(document_id) is None
    assert "delete_document" not in gateway.write_calls
    assert remote_id in {item.remote_id for item in asyncio.run(gateway.list_documents("repo-remote"))}


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
        "DELETE",
        f"/api/documents/{document_id}/remote",
        headers=auth_headers,
        json={"confirm": True},
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
        "DELETE",
        f"/api/documents/{document_id}/remote",
        headers=auth_headers,
        json={"confirm": True},
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
        "DELETE",
        f"/api/documents/{document_id}/remote",
        headers=auth_headers,
        json={"confirm": True},
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
    """Catches deletes getting stuck on a transient vector-store failure."""
    repository_id, gateway = _seed_repository(client)
    vector_store = _vector_store(client)
    created = client.post(
        f"/api/repositories/{repository_id}/documents",
        headers=auth_headers,
        json={"title": "State", "content": "# State\n\n状态管理"},
    )
    document_id = created.json()["id"]
    remote_id = client.app.state.document_store.get(document_id).remote_id
    assert remote_id
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
    # 这一行原先断言 `document_id not in {item.remote_id ...}` —— 拿本地 uuid 去比
    # 远端 id，两者永远不可能相等，所以它从来没红过。默认删除现在只清本地，该断言
    # 的对象是"是否留下了移出记录"，于是换成真的会红的那一条。
    assert client.app.state.removed_document_store.identities(repository_id) == {remote_id}
    assert remote_id in {item.remote_id for item in asyncio.run(gateway.list_documents("repo-remote"))}


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


def _seed_document_with_original(  # type: ignore[no-untyped-def]
    client, payload: bytes, *, document_id: str = "doc-original"
):
    """建一篇带原件的本地文档。

    直连 store 而不是走导入流程：这里要精确控制 `raw.bin` 的字节内容，
    导入链路会把它换成真实下载物。
    """
    repository = client.app.state.repository_store.create_local(name="Original KB")
    directory = client.app.state.settings.documents_dir / document_id
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    raw_path = directory / "raw.bin"
    markdown_path = directory / "document.md"
    raw_path.write_bytes(payload)
    markdown_path.write_text("# 标题\n\n正文", encoding="utf-8")
    return client.app.state.document_store.create(
        DocumentRecord(
            id=document_id,
            repository_id=repository.id,
            title="带原件的文档",
            raw_path=str(raw_path),
            markdown_path=str(markdown_path),
            source_type="import",
        )
    )


def _seed_document_without_original(client, *, document_id: str = "doc-plain"):  # type: ignore[no-untyped-def]
    repository = client.app.state.repository_store.create_local(name="No original")
    return client.app.state.document_store.create(
        DocumentRecord(id=document_id, repository_id=repository.id, title="无原件")
    )


def test_detail_reports_pdf_original(client, auth_headers) -> None:
    payload = b"%PDF-1.7\nrest"
    document = _seed_document_with_original(client, payload)

    response = client.get(f"/api/documents/{document.id}", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["originalMediaType"] == "application/pdf"
    assert body["originalByteSize"] == len(payload)


def test_detail_reports_no_original(client, auth_headers) -> None:
    document = _seed_document_without_original(client)

    response = client.get(f"/api/documents/{document.id}", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["originalMediaType"] is None
    assert body["originalByteSize"] is None


def test_detail_survives_missing_original_file(client, auth_headers) -> None:
    """记录里有 raw_path 但文件已被删 —— 不能因此让整篇文档读不出来。"""
    document = _seed_document_with_original(client, b"%PDF-1.7\nx", document_id="doc-gone")
    Path(document.raw_path or "").unlink()

    response = client.get(f"/api/documents/{document.id}", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["originalMediaType"] is None
    assert body["content"] == "# 标题\n\n正文"


def test_raw_serves_original_bytes(client, auth_headers) -> None:
    payload = b"%PDF-1.7\n" + b"x" * 4096
    document = _seed_document_with_original(client, payload)

    response = client.get(f"/api/documents/{document.id}/raw", headers=auth_headers)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.content == payload


def test_raw_honours_range_requests(client, auth_headers) -> None:
    """阅读器按需分页完全依赖这条；断言必须是 206 + 正确的 Content-Range。"""
    payload = b"%PDF-1.7\n" + bytes(range(256))
    document = _seed_document_with_original(client, payload)

    response = client.get(
        f"/api/documents/{document.id}/raw",
        headers={**auth_headers, "Range": "bytes=0-9"},
    )

    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes 0-9/{len(payload)}"
    assert response.content == payload[:10]


def test_raw_honours_tail_range(client, auth_headers) -> None:
    """尾部 range 单独测：pdf.js 读 trailer 时请求的是文件末尾。"""
    payload = b"%PDF-1.7\n" + bytes(range(256))
    document = _seed_document_with_original(client, payload)

    response = client.get(
        f"/api/documents/{document.id}/raw",
        headers={**auth_headers, "Range": "bytes=-16"},
    )

    assert response.status_code == 206
    assert response.content == payload[-16:]


def test_raw_rejects_unsatisfiable_range(client, auth_headers) -> None:
    document = _seed_document_with_original(client, b"%PDF-1.7\n")

    response = client.get(
        f"/api/documents/{document.id}/raw",
        headers={**auth_headers, "Range": "bytes=999-1999"},
    )

    assert response.status_code == 416


def test_raw_404s_without_original(client, auth_headers) -> None:
    document = _seed_document_without_original(client)

    response = client.get(f"/api/documents/{document.id}/raw", headers=auth_headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "DOCUMENT_ORIGINAL_MISSING"


def test_raw_404s_when_original_file_missing(client, auth_headers) -> None:
    document = _seed_document_with_original(client, b"%PDF-1.7\nx", document_id="doc-unlinked")
    Path(document.raw_path or "").unlink()

    response = client.get(f"/api/documents/{document.id}/raw", headers=auth_headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "DOCUMENT_ORIGINAL_MISSING"


def test_raw_404s_for_unknown_document(client, auth_headers) -> None:
    response = client.get("/api/documents/missing-document/raw", headers=auth_headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_raw_requires_token(client) -> None:
    document = _seed_document_with_original(client, b"%PDF-1.7\n")

    response = client.get(f"/api/documents/{document.id}/raw")

    assert response.status_code == 401


def test_raw_serves_non_pdf_original_as_octet_stream(client, auth_headers) -> None:
    """非 PDF 原件也照常返回，只是前端不会给它「原文」视图。"""
    payload = "# 我是 Markdown\n".encode()
    document = _seed_document_with_original(client, payload, document_id="doc-md")

    response = client.get(f"/api/documents/{document.id}/raw", headers=auth_headers)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.content == payload


# -- 插图（assets） -----------------------------------------------------------


def _seed_document_with_figure(client, *, document_id: str = "doc-figure"):  # type: ignore[no-untyped-def]
    """一篇带一张插图的文档：磁盘上有 PNG，chunk 里记着它。"""
    document = _seed_document_with_original(client, b"%PDF-1.7\nx", document_id=document_id)
    directory = client.app.state.settings.documents_dir / document.id / "assets"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    asset = "a" * 64 + ".png"
    (directory / asset).write_bytes(b"\x89PNG\r\n\x1a\nfigure")
    chunk = DocumentChunkRecord(
        id="chunk-1",
        document_id=document.id,
        repository_id=document.repository_id,
        chunk_index=0,
        text="正文",
        section_path="",
        page_number=3,
        token_count=2,
        source_url=None,
        images_json=json.dumps(
            [
                {
                    "asset": asset,
                    "pageNumber": 3,
                    "caption": "Voltage decay",
                    "width": 120,
                    "height": 80,
                    "bbox": [10.0, 20.0, 130.0, 100.0],
                    "fullPage": False,
                }
            ]
        ),
    )
    client.app.state.document_store.save_with_chunks(document, [chunk])
    return document, asset


def test_detail_lists_figures_with_position(client, auth_headers) -> None:
    """插图清单要带上页码与版面位置 —— 没有它们，前端无法把图和正文对上。"""
    document, asset = _seed_document_with_figure(client)

    response = client.get(f"/api/documents/{document.id}", headers=auth_headers)

    assert response.status_code == 200
    figures = response.json()["figures"]
    assert len(figures) == 1
    assert figures[0]["asset"] == asset
    assert figures[0]["pageNumber"] == 3
    assert figures[0]["caption"] == "Voltage decay"
    assert figures[0]["bbox"] == [10.0, 20.0, 130.0, 100.0]


def test_detail_reports_no_figures_as_empty_list(client, auth_headers) -> None:
    """没有图是常态，必须是空列表而不是 null —— 客户端不必先判空再遍历。"""
    document = _seed_document_without_original(client)

    response = client.get(f"/api/documents/{document.id}", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["figures"] == []


def test_detail_skips_a_corrupt_figure_record(client, auth_headers) -> None:
    """一条坏记录不该让整篇文档读不出来。"""
    document, _ = _seed_document_with_figure(client, document_id="doc-corrupt")
    original = client.app.state.document_store.list_chunks(document.id)[0]
    # 重新构造一条记录而不是改内存里那条：脱离 session 的对象改完再 merge 会引发
    # StaleDataError，那是测试自身的问题，与被测行为无关。
    corrupt = DocumentChunkRecord(
        id=original.id,
        document_id=document.id,
        repository_id=document.repository_id,
        chunk_index=original.chunk_index,
        text=original.text,
        section_path=original.section_path,
        page_number=original.page_number,
        token_count=original.token_count,
        source_url=original.source_url,
        images_json="{not json",
    )
    client.app.state.document_store.save_with_chunks(document, [corrupt])

    response = client.get(f"/api/documents/{document.id}", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["figures"] == []


def test_asset_serves_the_rendered_figure(client, auth_headers) -> None:
    document, asset = _seed_document_with_figure(client)

    response = client.get(
        f"/api/documents/{document.id}/assets/{asset}", headers=auth_headers
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")


def test_asset_rejects_a_traversal_name(client, auth_headers) -> None:
    """路径穿越必须挡在文件名白名单上，不能等到读文件。

    只在最后检查「解析后的路径是否在目录内」也能挡住，但白名单是第一道 —— 少了它，
    哪天目录判断被改动一个字符就成了任意文件读取。
    """
    document, _ = _seed_document_with_figure(client)

    for name in ("..%2F..%2Fraw.bin", "..%2fdocument.md", "....//raw.bin"):
        response = client.get(
            f"/api/documents/{document.id}/assets/{name}", headers=auth_headers
        )
        assert response.status_code == 404, name


def test_asset_rejects_a_name_outside_the_document_directory(client, auth_headers) -> None:
    """合法形状的文件名若指向别处，也必须拒绝 —— 第二道边界。"""
    document, _ = _seed_document_with_figure(client)
    outside = client.app.state.settings.documents_dir / "outside-asset.png"
    outside.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    outside.write_bytes(b"\x89PNG\r\n\x1a\noutside")

    response = client.get(
        f"/api/documents/{document.id}/assets/{outside.name}", headers=auth_headers
    )

    assert response.status_code == 404


def test_asset_reports_missing_file_as_not_found(client, auth_headers) -> None:
    document, asset = _seed_document_with_figure(client, document_id="doc-asset-gone")
    (
        client.app.state.settings.documents_dir / document.id / "assets" / asset
    ).unlink()

    response = client.get(
        f"/api/documents/{document.id}/assets/{asset}", headers=auth_headers
    )

    assert response.status_code == 404


def _seed_linked_document(client, original: Path, *, document_id: str = "doc-linked"):  # type: ignore[no-untyped-def]
    """建一篇**链接型**文档：原件在用户磁盘上，我们只记路径。"""
    repository = client.app.state.repository_store.create_local(name="Linked KB")
    directory = client.app.state.settings.documents_dir / document_id
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    markdown_path = directory / "document.md"
    markdown_path.write_text("# 标题\n\n正文", encoding="utf-8")
    return client.app.state.document_store.create(
        DocumentRecord(
            id=document_id,
            repository_id=repository.id,
            title="链接的文档",
            source_path=str(original),
            original_revision=f"{original.stat().st_mtime_ns}:{original.stat().st_size}",
            markdown_path=str(markdown_path),
            source_type="import",
        )
    )


def test_linked_original_is_served_from_the_users_own_file(
    client, auth_headers, tmp_path: Path
) -> None:
    """链接型的原件视图直接读用户的文件 —— 应用里没有第二份字节。"""
    original = tmp_path / "桌上的论文.pdf"
    original.write_bytes(b"%PDF-1.7\nlinked body")
    document = _seed_linked_document(client, original)

    detail = client.get(f"/api/documents/{document.id}", headers=auth_headers)
    assert detail.json()["originalMediaType"] == "application/pdf"
    assert detail.json()["originalByteSize"] == len(b"%PDF-1.7\nlinked body")

    response = client.get(f"/api/documents/{document.id}/raw", headers=auth_headers)
    assert response.status_code == 200
    assert response.content == b"%PDF-1.7\nlinked body"
    # 原件视图靠 Range 分页，链接型不能例外。
    assert response.headers["accept-ranges"] == "bytes"

    # 关键：应用里没有留下副本。
    assert not (client.app.state.settings.documents_dir / document.id / "raw.bin").exists()


def test_linked_original_that_moved_away_is_not_an_error(
    client, auth_headers, tmp_path: Path
) -> None:
    """文件被移走是**正常状态**：正文照读，只是没有原文可看。"""
    original = tmp_path / "会被移走的.pdf"
    original.write_bytes(b"%PDF-1.7\nbody")
    document = _seed_linked_document(client, original)
    original.unlink()

    detail = client.get(f"/api/documents/{document.id}", headers=auth_headers)

    assert detail.status_code == 200
    assert detail.json()["content"] == "# 标题\n\n正文"
    assert detail.json()["originalMediaType"] is None
    assert (
        client.get(f"/api/documents/{document.id}/raw", headers=auth_headers).status_code == 404
    )


def test_deleting_a_document_never_deletes_the_users_file(
    client, auth_headers, tmp_path: Path
) -> None:
    """不变量：应用只读原件。删除文档只允许动它自己的目录。"""
    original = tmp_path / "我的原件.pdf"
    original.write_bytes(b"%PDF-1.7\nmine")
    document = _seed_linked_document(client, original)

    response = client.request(
        "DELETE", f"/api/documents/{document.id}", headers=auth_headers, json={"confirm": True}
    )

    assert response.status_code == 204
    assert original.exists()
    assert original.read_bytes() == b"%PDF-1.7\nmine"
    assert not (client.app.state.settings.documents_dir / document.id).exists()


def test_linked_file_import_works_through_the_api(client, auth_headers, tmp_path: Path) -> None:
    """整条后端链路：请求里带签名 → 直读用户的文件 → 不留副本。

    这一条守的是**wire 上的字段名**：Python 侧是 `path_token`，线上是 `pathToken`
    （alias_generator=to_camel），TS 侧发的正是后者。名字对不上不会报错，只会让签名
    永远是 None —— 那意味着链接型导入在生产里彻底不可用，而单元测试全都还是绿的。
    """
    repository = client.app.state.repository_store.create_local(name="链接导入")
    original = tmp_path / "论文" / "attention.pdf"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"%PDF-1.7\nlinked")
    token = path_token(RUNTIME_TOKEN, FILE_DOMAIN, str(original))

    preview = client.post(
        "/api/imports/inspect",
        headers=auth_headers,
        json={"kind": "linked_file", "value": str(original), "name": "attention.pdf", "pathToken": token},
    )

    assert preview.status_code == 200
    body = preview.json()
    assert body["mediaType"] == "application/pdf"
    assert body["title"] == "attention.pdf"

    accepted = client.post(
        "/api/imports",
        headers=auth_headers,
        json={
            "source": {
                "kind": "linked_file",
                "value": str(original),
                "name": "attention.pdf",
                "pathToken": token,
            },
            "repositoryId": repository.id,
            "fingerprint": body["fingerprint"],
        },
    )

    assert accepted.status_code == 202, accepted.text


def test_linked_file_import_without_a_token_is_refused_through_the_api(
    client, auth_headers, tmp_path: Path
) -> None:
    """没有签名的路径在 API 层就被挡下 —— 渲染层编不出可用的请求。"""
    original = tmp_path / "id_rsa"
    original.write_text("PRIVATE KEY", encoding="utf-8")

    response = client.post(
        "/api/imports/inspect",
        headers=auth_headers,
        json={"kind": "linked_file", "value": str(original)},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "SOURCE_UNSUPPORTED"


def _pdf_with_text(text: str) -> bytes:
    """一张有文字的 PDF。重新转换要能解析出不同的正文，所以不能只改字节数。

    **只用 ASCII**：默认的 Helvetica 编码不了中文，插进去的字画不出来也抽不出文本，
    那一页会被判成"需要 OCR"，于是这条用例开始跑视觉模型（实测从 0.1 秒变成几分钟）。
    """
    assert text.isascii(), "测试 PDF 用 ASCII 文本，避免踩到 OCR 那条慢路径"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 96), text)
    raw = document.tobytes()
    document.close()
    return raw


@pytest.fixture
def indexed_client(client):
    """装上假嵌入 provider 的 client。

    `client` fixture 起的是**真的** BGE provider，而任何触发索引的请求都要为它付一次
    模型加载 —— 本机实测一次 **519 秒**，足以让"重新转换"这类用例看起来像是坏了。
    多数既有用例跑得快，是因为同一个 pytest 进程里模型只会加载一次；单独跑某一条用例时
    这笔钱就得它自己出。需要索引的用例显式依赖这个 fixture，把代价固定成 0。
    """
    client.app.state.embedding_provider = FakeEmbeddingProvider(
        client.app.state.settings.embedding_settings
    )
    return client


def _signed(path: Path) -> str:
    return path_token(RUNTIME_TOKEN, FILE_DOMAIN, str(path))


def _touch(path: Path, *, shift_seconds: int = 5) -> None:
    """把 mtime 挪一下 —— 变更检测看的正是 mtime_ns:size。"""
    info = path.stat()
    os.utime(path, (info.st_atime, info.st_mtime + shift_seconds))


def test_original_state_reports_a_linked_original_that_matches(
    client, auth_headers, tmp_path: Path
) -> None:
    original = tmp_path / "论文.pdf"
    original.write_bytes(b"%PDF-1.7\nbody")
    document = _seed_linked_document(client, original)

    body = client.get(f"/api/documents/{document.id}", headers=auth_headers).json()

    assert body["originalState"] == "available"


def test_original_state_flags_a_changed_original(
    client, auth_headers, tmp_path: Path
) -> None:
    """原件被改过 —— 提示"要重新转换吗"，而不是继续假装正文是最新的。

    这条用例因为「不做变更检测」而红（状态永远是 available）。
    """
    original = tmp_path / "论文.pdf"
    original.write_bytes(b"%PDF-1.7\nbody")
    document = _seed_linked_document(client, original)

    _touch(original)

    body = client.get(f"/api/documents/{document.id}", headers=auth_headers).json()
    assert body["originalState"] == "changed"


def test_original_state_separates_missing_from_none(
    client, auth_headers, tmp_path: Path
) -> None:
    """「原件丢了」与「本来就没有原件」必须分得开。

    合成一个状态的话，前端只能显示"没有原文"，用户分不清是自己没导入原件、
    还是文件被挪走了（后者要给他一个重新定位的入口）。
    """
    original = tmp_path / "论文.pdf"
    original.write_bytes(b"%PDF-1.7\nbody")
    linked = _seed_linked_document(client, original)
    plain = _seed_document_without_original(client, document_id="doc-no-original")

    original.unlink()

    assert (
        client.get(f"/api/documents/{linked.id}", headers=auth_headers).json()["originalState"]
        == "missing"
    )
    assert (
        client.get(f"/api/documents/{plain.id}", headers=auth_headers).json()["originalState"]
        == "none"
    )


def test_relinking_a_moved_file_returns_to_available(
    client, auth_headers, tmp_path: Path
) -> None:
    """文件被挪走（内容没变）→ 重新定位之后应当直接可用，不该再喊"已更新"。

    这就是重新定位**不更新** revision 的原因：内容还是一个字节没变的那一份。
    """
    folder = tmp_path / "论文"
    folder.mkdir()
    original = folder / "论文.pdf"
    original.write_bytes(b"%PDF-1.7\nbody")
    document = _seed_linked_document(client, original)

    moved = tmp_path / "归档"
    moved.mkdir()
    relocated = moved / "论文.pdf"
    # shutil.move 会保留 mtime；这正是"用户只是把文件挪了个地方"的形状。
    shutil.move(str(original), str(relocated))
    assert client.get(f"/api/documents/{document.id}", headers=auth_headers).json()[
        "originalState"
    ] == "missing"

    response = client.post(
        f"/api/documents/{document.id}/original",
        headers=auth_headers,
        json={"path": str(relocated), "pathToken": _signed(relocated)},
    )

    assert response.status_code == 200
    assert response.json()["originalState"] == "available"


def test_relinking_to_a_different_file_asks_for_reconversion(
    client, auth_headers, tmp_path: Path
) -> None:
    """换成另一个文件 → 绑定成功，但状态是 changed：正文还是旧那一版转换出来的。"""
    original = tmp_path / "旧.pdf"
    original.write_bytes(b"%PDF-1.7\nold")
    document = _seed_linked_document(client, original)
    replacement = tmp_path / "新.pdf"
    replacement.write_bytes(b"%PDF-1.7\nnew content here")

    body = client.post(
        f"/api/documents/{document.id}/original",
        headers=auth_headers,
        json={"path": str(replacement), "pathToken": _signed(replacement)},
    ).json()

    assert body["originalState"] == "changed"


def test_relinking_without_a_signature_is_refused(
    client, auth_headers, tmp_path: Path
) -> None:
    """重新定位是第二条"把文档指向某个文件"的路，门必须和导入一样严。"""
    original = tmp_path / "论文.pdf"
    original.write_bytes(b"%PDF-1.7\nbody")
    document = _seed_linked_document(client, original)
    # 扩展名必须是**受支持**的那一种：否则光是扩展名检查就能挡住它，
    # 这条用例就测不到"没有签名"这一条了（而签名才是真正的门）。
    secret_file = tmp_path / "其实不是论文.pdf"
    secret_file.write_bytes(b"%PDF-1.7\nsecret")

    response = client.post(
        f"/api/documents/{document.id}/original",
        headers=auth_headers,
        json={"path": str(secret_file)},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "SOURCE_UNSUPPORTED"
    stored = client.app.state.document_store.get(document.id)
    assert stored is not None and stored.source_path == str(original)


def test_reconvert_reads_the_original_again_and_clears_the_changed_flag(
    indexed_client, auth_headers, tmp_path: Path
) -> None:
    """重新转换：重新读原件 → 走编辑保存那条流水线 → 状态回到 available。

    三件事一起断言，因为它们各自都可能悄悄失效：正文真的变了、版本快照留下了、
    状态没有继续喊"已更新"（那会让用户以为点了没用）。
    """
    original = tmp_path / "论文.pdf"
    original.write_bytes(_pdf_with_text("version one"))
    document = _seed_linked_document(indexed_client, original)
    # 用户改了原件。
    original.write_bytes(_pdf_with_text("version two completely different"))
    assert indexed_client.get(f"/api/documents/{document.id}", headers=auth_headers).json()[
        "originalState"
    ] == "changed"

    response = indexed_client.post(
        f"/api/documents/{document.id}/reconvert", headers=auth_headers, json={}
    )

    assert response.status_code == 200
    body = response.json()
    assert "version two" in body["content"]
    assert body["originalState"] == "available"
    versions = indexed_client.app.state.version_store.list(document.id)
    assert [version.content for version in versions] == ["# 标题\n\n正文"]


def test_reconvert_refuses_a_document_without_a_bound_original(
    indexed_client, auth_headers
) -> None:
    """拷贝型文档没有"原件被改"这回事：那份字节是我们自己管的。"""
    document = _seed_document_without_original(indexed_client, document_id="doc-copy-only")

    response = indexed_client.post(
        f"/api/documents/{document.id}/reconvert", headers=auth_headers, json={}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DOCUMENT_ORIGINAL_NOT_LINKED"


def test_reconvert_reports_a_missing_original_as_such(
    indexed_client, auth_headers, tmp_path: Path
) -> None:
    """原件没了就说原件没了（404 + 原因码），不要退化成 500 或"解析失败"。"""
    original = tmp_path / "论文.pdf"
    original.write_bytes(_pdf_with_text("some content"))
    document = _seed_linked_document(indexed_client, original)
    original.unlink()

    response = indexed_client.post(
        f"/api/documents/{document.id}/reconvert", headers=auth_headers, json={}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "DOCUMENT_ORIGINAL_MISSING"
    # 正文仍然是可读的 —— 原件丢了不该让文档打不开。
    assert (
        indexed_client.get(f"/api/documents/{document.id}", headers=auth_headers).json()["content"]
        == "# 标题\n\n正文"
    )


def test_original_location_gives_main_a_signed_path(
    client, auth_headers, tmp_path: Path
) -> None:
    """主进程要的位置必须带**能验的签名**。

    签名不是装饰：没有它，主进程就只是照着后端说的路径去打开文件，而"渲染层不能指名
    任意文件"在最需要它的那一侧（真正去动文件系统的那个进程）变成空壳。
    """
    original = tmp_path / "论文.pdf"
    original.write_bytes(b"%PDF-1.7\nbody")
    document = _seed_linked_document(client, original)

    body = client.get(
        f"/api/documents/{document.id}/original-location", headers=auth_headers
    ).json()

    assert body["path"] == str(original)
    assert body["state"] == "available"
    # 用后端的验证函数验它自己发的签名：换路径就不该通过。
    secret = client.app.state.settings.session_token.get_secret_value()
    assert path_token_matches(secret, FILE_DOMAIN, str(original), body["pathToken"])
    assert not path_token_matches(
        secret, FILE_DOMAIN, str(tmp_path / "别的文件.pdf"), body["pathToken"]
    )


def test_original_location_separates_missing_from_never_linked(
    client, auth_headers, tmp_path: Path
) -> None:
    """「本来没有原件」与「绑定过但丢了」必须继续可区分。

    两者的界面出口不同：前者什么都不该给，后者要给「重新定位原件」。若这里对两者都报
    404，那个区分就在最后一跳被抹掉了。
    """
    gone = tmp_path / "被移走的.pdf"
    gone.write_bytes(b"%PDF-1.7\nbody")
    linked = _seed_linked_document(client, gone)
    gone.unlink()

    missing = client.get(
        f"/api/documents/{linked.id}/original-location", headers=auth_headers
    ).json()
    assert missing == {"path": None, "pathToken": None, "state": "missing"}

    # 本地新建的文档：没有绑定过原件。
    repository = client.app.state.repository_store.create_local(name="Plain KB")
    plain = client.app.state.document_store.create(
        DocumentRecord(
            id="doc-plain",
            repository_id=repository.id,
            title="手写的",
            markdown_path=str(tmp_path / "none.md"),
            source_type="manual",
        )
    )
    none = client.get(
        f"/api/documents/{plain.id}/original-location", headers=auth_headers
    ).json()
    assert none == {"path": None, "pathToken": None, "state": "none"}


def _create_remote_document(client, auth_headers, repository_id: str, title: str) -> tuple[str, str]:
    """在远端知识库里建一篇文档，返回 (本地 id, 远端 id)。"""
    created = client.post(
        f"/api/repositories/{repository_id}/documents",
        headers=auth_headers,
        json={"title": title, "content": f"# {title}\n\n{title} 的正文"},
    )
    assert created.status_code == 201, created.text
    document_id = created.json()["id"]
    document = client.app.state.document_store.get(document_id)
    assert document is not None and document.remote_id
    return document_id, document.remote_id


def _create_local_documents(client, auth_headers, count: int) -> list[dict]:
    """建 ``count`` 篇本地文档（标题 第一篇/第二篇/…），返回创建响应。"""
    titles = ["第一篇", "第二篇", "第三篇", "第四篇", "第五篇"]
    repository = client.app.state.repository_store.create_local(name="本地库")
    client.app.state.embedding_provider = FakeEmbeddingProvider(
        client.app.state.settings.embedding_settings
    )
    documents = []
    for title in titles[:count]:
        created = client.post(
            f"/api/repositories/{repository.id}/documents",
            headers=auth_headers,
            json={"title": title, "content": f"# 引言\n\n{title} 的正文"},
        )
        assert created.status_code == 201, created.text
        documents.append(created.json())
    return documents


def _seed_shared_graph(client, documents: list[dict]) -> None:
    """按 extractor 的真实形状给这些文档写图：都提到同一个主题「引言」。"""
    store = client.app.state.graph_store
    for document in documents:
        store.replace_document_graph(
            document["id"],
            [
                GraphNode(
                    id=document["id"],
                    kind="document",
                    label=document["title"],
                    document_id=document["id"],
                ),
                GraphNode(id="topic:引言", kind="topic", label="引言", document_id=document["id"]),
            ],
            [GraphEdge(source_id=document["id"], target_id="topic:引言", relation="mentions")],
        )


def test_removing_a_document_takes_its_version_history_and_graph_nodes(
    client, auth_headers
) -> None:
    """移除必须带走版本历史与图谱节点 —— 两者都没有外键，漏了不会报任何错。

    版本这一条是内容层面的：``/api/documents/{id}/versions`` 不检查文档是否存在，
    少了这步，用户在界面上"移除"过的正文仍然读得出来。图谱这一条是可见层面的：
    已移除文档的标题与页内标题会继续列在「图谱」页。共享节点必须留下 —— 它还在不在
    取决于有没有别的文档引用它，而不是谁先写了它。
    """
    first, second = _create_local_documents(client, auth_headers, 2)
    assert (
        client.put(
            f"/api/documents/{first['id']}",
            headers=auth_headers,
            json={"title": "第一篇", "content": "# 引言\n\n改过的正文"},
        ).status_code
        == 200
    )
    _seed_shared_graph(client, [first, second])

    # 前置条件：确实有东西可丢。否则下面的断言在"本来就什么都没有"时也会绿。
    assert client.get(f"/api/documents/{first['id']}/versions", headers=auth_headers).json()
    assert len(client.get("/api/graph/nodes", headers=auth_headers).json()) == 3

    response = client.request(
        "DELETE", f"/api/documents/{first['id']}", headers=auth_headers, json={"confirm": True}
    )

    assert response.status_code == 204
    assert client.get(f"/api/documents/{first['id']}/versions", headers=auth_headers).json() == []
    assert {node["id"] for node in client.get("/api/graph/nodes", headers=auth_headers).json()} == {
        second["id"],
        "topic:引言",
    }
    assert [
        edge["sourceId"]
        for edge in client.get("/api/graph/nodes/topic:引言/adjacency", headers=auth_headers).json()
    ] == [second["id"]]


def test_batch_remove_takes_versions_and_graph_nodes_of_every_selected_document(
    client, auth_headers
) -> None:
    """批量移除走的是另一条循环，派生数据同样一篇都不能漏。

    只报一个"移除了 2 篇"而留下两篇的版本历史和图谱节点，是这条路径最容易出现的
    半成品：界面看起来干净了，内容还在。
    """
    documents = _create_local_documents(client, auth_headers, 2)
    for document in documents:
        assert (
            client.put(
                f"/api/documents/{document['id']}",
                headers=auth_headers,
                json={"title": document["title"], "content": "# 引言\n\n改过的正文"},
            ).status_code
            == 200
        )
    _seed_shared_graph(client, documents)
    repository_id = documents[0]["repositoryId"]

    response = client.post(
        f"/api/repositories/{repository_id}/documents/remove",
        headers=auth_headers,
        json={"documentIds": [document["id"] for document in documents]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["removed"] == 2
    for document in documents:
        assert (
            client.get(f"/api/documents/{document['id']}/versions", headers=auth_headers).json()
            == []
        )
    assert client.get("/api/graph/nodes", headers=auth_headers).json() == []



def test_batch_remove_takes_the_selected_documents_and_records_each_removal(
    client, auth_headers
) -> None:
    """批量移除：选中的走，没选中的留，而且每一篇都留下移出记录。

    留下记录这件事是批量路径最容易漏的：单篇移除会写，批量若只删本地行，
    那些文档会在下次远端更新时集体回来 —— 而且是静默的。
    """
    repository_id, _ = _seed_repository(client)
    keep_id, _ = _create_remote_document(client, auth_headers, repository_id, "留下")
    first_id, first_remote = _create_remote_document(client, auth_headers, repository_id, "第一篇")
    second_id, second_remote = _create_remote_document(
        client, auth_headers, repository_id, "第二篇"
    )

    response = client.post(
        f"/api/repositories/{repository_id}/documents/remove",
        headers=auth_headers,
        json={"documentIds": [first_id, second_id]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["removed"] == 2
    store = client.app.state.document_store
    assert store.get(first_id) is None
    assert store.get(second_id) is None
    assert store.get(keep_id) is not None
    assert client.app.state.removed_document_store.identities(repository_id) == {
        first_remote,
        second_remote,
    }
    assert store.list_chunks(first_id) == []


def test_batch_remove_ignores_ids_belonging_to_another_repository(
    client, auth_headers
) -> None:
    """跨库的 id 必须被忽略，而不是被"顺手删掉"或让整批失败。

    批量操作里最常见的脏输入就是过期/错库的 id；整批因此失败会让用户反复重试，
    而默默删掉另一个知识库的文档是数据损失。
    """
    repository_id, _ = _seed_repository(client)
    other_repository = client.app.state.repository_store.create_local(name="另一个库")
    mine_id, mine_remote = _create_remote_document(client, auth_headers, repository_id, "我的")
    foreign_id, _ = _create_remote_document(client, auth_headers, repository_id, "别处的")
    client.app.state.document_store.delete_local(foreign_id)
    with client.app.state.database.session() as session:
        session.add(
            DocumentRecord(
                id=foreign_id,
                repository_id=other_repository.id,
                title="别处的",
                source_type="local",
                status="pending",
            )
        )

    response = client.post(
        f"/api/repositories/{repository_id}/documents/remove",
        headers=auth_headers,
        json={"documentIds": [mine_id, foreign_id, "00000000-0000-4000-8000-000000000000"]},
    )

    assert response.status_code == 200
    assert response.json()["removed"] == 1
    assert client.app.state.document_store.get(mine_id) is None
    assert client.app.state.document_store.get(foreign_id) is not None
    assert client.app.state.removed_document_store.identities(repository_id) == {mine_remote}


def test_restore_drops_the_stale_sync_snapshot_so_the_document_can_come_back(
    client, auth_headers
) -> None:
    """恢复必须清掉那份陈旧快照，否则远端没变过的文档永远回不来。

    同步靠 `repository_sync_state` 判重：快照还在、远端内容又恰好没变过，那次同步
    会把它当 `unchanged` 跳过。恢复只删移除记录而不动快照，看起来"恢复成功"，
    文档却再也不会出现。
    """
    repository_id, _ = _seed_repository(client)
    document_id, remote_id = _create_remote_document(client, auth_headers, repository_id, "待恢复")
    sync_state = client.app.state.sync_state_store
    sync_state.upsert(repository_id, remote_id, "待恢复", "hash-1", "https://remote.local/x")
    assert client.request(
        "DELETE", f"/api/documents/{document_id}", headers=auth_headers, json={"confirm": True}
    ).status_code == 204

    removed = client.get(
        f"/api/repositories/{repository_id}/removed-documents", headers=auth_headers
    ).json()
    assert len(removed) == 1
    assert removed[0]["title"] == "待恢复"
    assert removed[0]["remoteId"] == remote_id
    assert remote_id in sync_state.get_snapshot(repository_id)

    restored = client.post(
        f"/api/repositories/{repository_id}/removed-documents/{removed[0]['id']}/restore",
        headers=auth_headers,
    )

    assert restored.status_code == 200, restored.text
    assert restored.json()["remoteId"] == remote_id
    assert (
        client.get(
            f"/api/repositories/{repository_id}/removed-documents", headers=auth_headers
        ).json()
        == []
    )
    # 真正让文档能回来的那一步：
    assert remote_id not in sync_state.get_snapshot(repository_id)


def test_local_document_removal_leaves_no_removal_record(client, auth_headers) -> None:
    """本地文档不会被同步拉回来，所以不该留记录。

    记了的话「已移除的文档」会攒下一堆恢复不了的条目 —— 恢复只对远端身份有意义。
    """
    repository = client.app.state.repository_store.create_local(name="本地库")
    client.app.state.embedding_provider = FakeEmbeddingProvider(
        client.app.state.settings.embedding_settings
    )
    created = client.post(
        f"/api/repositories/{repository.id}/documents",
        headers=auth_headers,
        json={"title": "本地笔记", "content": "# 本地笔记\n\n正文"},
    )
    assert created.status_code == 201, created.text
    document_id = created.json()["id"]
    assert client.app.state.document_store.get(document_id).repository_id == repository.id

    response = client.request(
        "DELETE", f"/api/documents/{document_id}", headers=auth_headers, json={"confirm": True}
    )

    assert response.status_code == 204
    assert client.app.state.document_store.get(document_id) is None
    assert (
        client.get(
            f"/api/repositories/{repository.id}/removed-documents", headers=auth_headers
        ).json()
        == []
    )


def test_explicit_remote_delete_removes_both_sides_and_blocks_reimport(
    client, auth_headers
) -> None:
    """`/remote` 那条路才允许动远端原文，并且一并登记移出（防止远端软删留下复活的可能）。"""
    repository_id, gateway = _seed_repository(client)
    document_id, remote_id = _create_remote_document(client, auth_headers, repository_id, "删远端")

    response = client.request(
        "DELETE",
        f"/api/documents/{document_id}/remote",
        headers=auth_headers,
        json={"confirm": True},
    )

    assert response.status_code == 204
    assert "delete_document" in gateway.write_calls
    assert remote_id not in {
        item.remote_id for item in asyncio.run(gateway.list_documents("repo-remote"))
    }
    assert client.app.state.document_store.get(document_id) is None
    assert client.app.state.removed_document_store.identities(repository_id) == {remote_id}


def test_removed_documents_list_is_scoped_to_its_repository(client, auth_headers) -> None:
    """列表按知识库取，别把别的库的移出记录混进来。"""
    repository_id, _ = _seed_repository(client)
    other_repository = client.app.state.repository_store.create_local(name="另一个库")
    document_id, _ = _create_remote_document(client, auth_headers, repository_id, "甲")
    client.request(
        "DELETE", f"/api/documents/{document_id}", headers=auth_headers, json={"confirm": True}
    )

    mine = client.get(
        f"/api/repositories/{repository_id}/removed-documents", headers=auth_headers
    )
    other = client.get(
        f"/api/repositories/{other_repository.id}/removed-documents", headers=auth_headers
    )

    assert len(mine.json()) == 1
    assert other.json() == []


def test_restore_rejects_a_removal_id_from_another_repository(client, auth_headers) -> None:
    """跨库恢复必须 404：否则一个库的恢复按钮能操作另一个库的记录。"""
    repository_id, _ = _seed_repository(client)
    other_repository = client.app.state.repository_store.create_local(name="另一个库")
    document_id, _ = _create_remote_document(client, auth_headers, repository_id, "甲")
    client.request(
        "DELETE", f"/api/documents/{document_id}", headers=auth_headers, json={"confirm": True}
    )
    removal_id = client.get(
        f"/api/repositories/{repository_id}/removed-documents", headers=auth_headers
    ).json()[0]["id"]

    response = client.post(
        f"/api/repositories/{other_repository.id}/removed-documents/{removal_id}/restore",
        headers=auth_headers,
    )

    assert response.status_code == 404
    # 记录必须还在原处，不能被这次越权请求消耗掉。
    assert (
        len(
            client.get(
                f"/api/repositories/{repository_id}/removed-documents", headers=auth_headers
            ).json()
        )
        == 1
    )


def test_documents_list_reports_the_remote_directory_path_on_the_wire(
    client, auth_headers
) -> None:
    """目录路径必须真的出现在文档列表响应里。

    这条守的是"名字对不上不会报错"那类静默失败：渲染层的 schema 把 `path` 当成
    可选（缺失就是空数组），所以后端漏送这一项时界面只会安静地平铺显示，没有任何
    地方会红。断言的是**响应里的字段**，不是数据库里的列。
    """
    # _seed_repository 除了建远端库，还会装上假嵌入模型 —— 少了它，下面建文档这步
    # 会去 HuggingFace 下 bge-m3，在没有网的环境里表现为"卡住"而不是失败。
    repository_id, _gateway = _seed_repository(client)
    document_id, remote_id = _create_remote_document(client, auth_headers, repository_id, "甲")
    document = client.app.state.document_store.get(document_id)
    with client.app.state.database.session() as session:
        record = session.get(DocumentRecord, document_id)
        record.remote_path_json = json.dumps(["第一章", "第一节"])
    assert document is not None

    summaries = client.get(
        f"/api/repositories/{repository_id}/documents", headers=auth_headers
    ).json()

    assert [item["path"] for item in summaries] == [["第一章", "第一节"]]
    detail = client.get(f"/api/documents/{document_id}", headers=auth_headers).json()
    assert detail["path"] == ["第一章", "第一节"]
    # 本地文档没有远端目录，恒为空列表（不是缺失）。
    local_repository = client.app.state.repository_store.create_local(name="本地库")
    local = client.post(
        f"/api/repositories/{local_repository.id}/documents",
        headers=auth_headers,
        json={"title": "本地笔记", "content": "# 本地笔记\n\n正文"},
    ).json()
    assert local["path"] == []
    assert remote_id
