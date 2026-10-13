from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.api.errors import DomainError
from app.core.embedding import FakeEmbeddingProvider
from app.remote.fake import FakeRemoteProvider
from app.remote.provider import ProviderCapabilities, ProviderIdentity
from app.storage.models import (
    DistillationRecord,
    DocumentVersionRecord,
    GraphEdgeRecord,
    GraphNodeRecord,
    ImportJobRecord,
    ImportStatus,
    MemoryChunkRecord,
    RemovedRemoteDocumentRecord,
    SessionRecord,
    SessionSummaryRecord,
)
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


def _local_repository_with_document(client, auth_headers, name: str = "本地库") -> tuple[str, str]:
    created = client.post("/api/repositories", headers=auth_headers, json={"name": name})
    repository_id = created.json()["id"]
    client.app.state.embedding_provider = FakeEmbeddingProvider(
        client.app.state.settings.embedding_settings
    )
    document = client.post(
        f"/api/repositories/{repository_id}/documents",
        headers=auth_headers,
        json={"title": "论文", "content": "# 标题\n\n第一段内容足够长。\n\n第二段内容也足够长。"},
    )
    assert document.status_code == 201, document.text
    return repository_id, document.json()["id"]


def _delete(client, auth_headers, repository_id: str, *, confirm: bool = True):  # type: ignore[no-untyped-def]
    return client.request(
        "DELETE",
        f"/api/repositories/{repository_id}",
        headers=auth_headers,
        json={"confirm": confirm},
    )


def _rename(client, auth_headers, repository_id: str, name: str):  # type: ignore[no-untyped-def]
    return client.post(
        f"/api/repositories/{repository_id}/rename",
        headers=auth_headers,
        json={"name": name},
    )


def test_rename_local_repository_changes_only_the_name(client, auth_headers) -> None:
    """本地知识库改名：名字变了，库里的文档一篇都不能少。"""
    repository_id, document_id = _local_repository_with_document(client, auth_headers)

    response = _rename(client, auth_headers, repository_id, "新名字")

    assert response.status_code == 200, response.text
    assert response.json()["scope"] == "local_only"
    assert response.json()["repository"]["name"] == "新名字"
    record = client.app.state.repository_store.get(repository_id)
    assert record.name == "新名字"
    assert record.provider is None
    assert client.app.state.document_store.get(document_id) is not None
    # 改名不是重新绑定：远端身份不能被动到（本地库本来就没有）。
    assert record.remote_id is None


def test_rename_repository_writes_back_when_the_provider_can(
    client, auth_headers, remote_provider: FakeRemoteProvider
) -> None:
    """远端支持改名时，远端那一半必须真的改掉，并且以远端返回的名字为准。

    只改本地的话，下一次列表刷新就会把远端旧名字拉回来 —— 用户看到"改名自己弹
    回去了"。所以断言的是**远端状态**，不是响应体。
    """
    remote_provider.seed_repository("repo-remote", "SwiftUI")
    listed = client.get("/api/repositories", headers=auth_headers).json()[0]

    response = _rename(client, auth_headers, listed["id"], "SwiftUI 笔记")

    assert response.status_code == 200, response.text
    assert response.json()["scope"] == "remote"
    assert response.json()["repository"]["name"] == "SwiftUI 笔记"
    assert remote_provider.write_calls == ["rename_repository"]
    # 刷新列表（远端名字会被 upsert 回本地）之后名字仍然是我们改的那个。
    refreshed = client.get("/api/repositories", headers=auth_headers).json()[0]
    assert refreshed["name"] == "SwiftUI 笔记"


def test_rename_repository_without_the_capability_stays_local(
    client, auth_headers
) -> None:
    """provider 不支持改名时：本地改名要成功，但必须如实说"只在本地生效"。

    报错是错的（名字其实已经改了），静默当成成功也是错的（刷新就变回去）。
    """
    provider = FakeRemoteProvider()
    provider.identity = ProviderIdentity(
        name="yuque",
        label="语雀",
        capabilities=ProviderCapabilities(
            browser_install=True,
            marker_lookup=True,
            document_paths=True,
            repository_rename=False,
            document_rename=True,
        ),
    )
    provider.seed_repository("repo-remote", "SwiftUI")
    install_remote_provider(client.app, provider)
    listed = client.get("/api/repositories", headers=auth_headers).json()[0]

    response = _rename(client, auth_headers, listed["id"], "本地新名")

    assert response.status_code == 200, response.text
    assert response.json()["scope"] == "local_only"
    assert response.json()["repository"]["name"] == "本地新名"
    assert provider.write_calls == []
    # 远端没改，所以下一次刷新会把远端名字写回来 —— 这正是界面必须提示的后果。
    refreshed = client.get("/api/repositories", headers=auth_headers).json()[0]
    assert refreshed["name"] == "SwiftUI"


def test_rename_repository_falls_back_to_local_when_the_live_session_cannot(
    client, auth_headers
) -> None:
    """能力声明说"能改"，当前会话却改不了 —— 必须降级成"只改本地"，而不是报错。

    这不是臆想的组合：语雀的 `repository_rename` 是**来源级**声明（API 会话能改），
    而实际生效的可能是网页会话（没有 API 凭据，改不了）。provider 已经如实抛出
    ``REMOTE_CAPABILITY_UNSUPPORTED``，接口就该退回 `local_only`：本地改名已经完成，
    报一个失败框只会让用户以为名字没改。
    """

    class WebOnlyProvider(FakeRemoteProvider):
        async def rename_repository(self, repository_id: str, name: str):  # type: ignore[no-untyped-def]
            raise DomainError("REMOTE_CAPABILITY_UNSUPPORTED", "网页会话不支持重命名", 400, False)

    provider = WebOnlyProvider()
    provider.seed_repository("repo-remote", "SwiftUI")
    install_remote_provider(client.app, provider)
    listed = client.get("/api/repositories", headers=auth_headers).json()[0]

    response = _rename(client, auth_headers, listed["id"], "本地新名")

    assert response.status_code == 200, response.text
    assert response.json()["scope"] == "local_only"
    assert response.json()["repository"]["name"] == "本地新名"
    assert client.app.state.repository_store.get(listed["id"]).name == "本地新名"


def test_rename_repository_reports_a_remote_failure_without_silently_succeeding(
    client, auth_headers, remote_provider: FakeRemoteProvider
) -> None:
    """远端改名失败必须是一个错误，不能被"本地已经改了"糊过去。"""

    async def refuse(_repository_id: str, _name: str):  # type: ignore[no-untyped-def]
        raise RuntimeError("remote rejected the rename")

    remote_provider.rename_repository = refuse  # type: ignore[method-assign]
    remote_provider.seed_repository("repo-remote", "SwiftUI")
    listed = client.get("/api/repositories", headers=auth_headers).json()[0]

    response = _rename(client, auth_headers, listed["id"], "新名字")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "REMOTE_OPERATION_FAILED"
    assert client.app.state.repository_store.get(listed["id"]).name == "SwiftUI"


def test_rename_repository_rejects_a_blank_name(client, auth_headers) -> None:
    created = client.post("/api/repositories", headers=auth_headers, json={"name": "本地"})

    response = _rename(client, auth_headers, created.json()["id"], "   ")

    assert response.status_code == 422
    assert client.app.state.repository_store.get(created.json()["id"]).name == "本地"


def test_rename_repository_is_not_found_for_unknown_id(client, auth_headers) -> None:
    response = _rename(
        client, auth_headers, "00000000-0000-4000-8000-000000000000", "新名字"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_delete_repository_requires_explicit_confirmation(client, auth_headers) -> None:
    """没有 confirm 就必须是 400，而且必须真的什么都没删。"""
    repository_id, _ = _local_repository_with_document(client, auth_headers)

    refused = _delete(client, auth_headers, repository_id, confirm=False)

    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "CONFIRMATION_REQUIRED"
    assert client.app.state.repository_store.get(repository_id) is not None


def test_delete_repository_removes_documents_chunks_vectors_and_files(client, auth_headers) -> None:
    """删除知识库要连文档、分块、向量和磁盘目录一起带走。

    这四条断言各自都对应一种"删了但没删干净"：只删仓库行会留下悬空文档；
    只删文档行会留下分块；只删 SQLite 会留下向量；不删目录会留下正文与插图。
    """
    repository_id, document_id = _local_repository_with_document(client, auth_headers)
    document_store = client.app.state.document_store
    markdown = Path(document_store.get(document_id).markdown_path)
    assert document_store.list_chunks(document_id)
    assert client.app.state.vector_store.list_stored(repository_id)
    assert markdown.is_file()

    response = _delete(client, auth_headers, repository_id)

    assert response.status_code == 204
    assert client.app.state.repository_store.get(repository_id) is None
    assert document_store.get(document_id) is None
    assert document_store.list_chunks(document_id) == []
    assert client.app.state.vector_store.list_stored(repository_id) == []
    assert not markdown.parent.exists()


def test_delete_repository_queues_vector_cleanup_when_collection_drop_fails(
    client, auth_headers, monkeypatch
) -> None:
    """集合整删失败时，向量 id 必须落进清理队列，而不是随仓库行一起消失。

    这是唯一"会泄漏"的失败模式：仓库行删掉后没人再知道这些 id，下次启动也就
    没有重放的依据。断言的是**队列里有这些 id**，而不是"请求没崩"。
    """
    repository_id, document_id = _local_repository_with_document(client, auth_headers)
    expected_ids = {
        chunk.vector_id
        for chunk in client.app.state.document_store.list_chunks(document_id)
        if chunk.vector_id
    }
    assert expected_ids
    vector_store = client.app.state.vector_store

    def refuse(_repository_id: str) -> None:
        raise RuntimeError("collection drop failed")

    monkeypatch.setattr(vector_store, "delete_collection", refuse)

    response = _delete(client, auth_headers, repository_id)

    assert response.status_code == 204
    queued = client.app.state.vector_cleanup_store.list()
    assert len(queued) == 1
    assert queued[0].document_id == document_id
    assert set(json.loads(queued[0].vector_ids_json)) == expected_ids


def test_delete_repository_refuses_while_an_import_is_active(client, auth_headers) -> None:
    """导入正在往这个仓库写 documents/chunks，此时删除只会让导入撞外键报错。"""
    repository_id, _ = _local_repository_with_document(client, auth_headers)
    client.app.state.import_job_store.create(
        ImportJobRecord(
            source_kind="staged_file",
            source_value="/tmp/source.md",
            repository_id=repository_id,
            state=ImportStatus.PARSING,
        )
    )

    refused = _delete(client, auth_headers, repository_id)

    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "REPOSITORY_BUSY"
    assert client.app.state.repository_store.get(repository_id) is not None


def test_delete_repository_refuses_while_a_document_mutation_is_pending(
    client, auth_headers, remote_provider: FakeRemoteProvider
) -> None:
    """在飞的远端变更同样要拦住；这条记录只有补偿完成才会消失。"""
    remote_provider.seed_repository("repo-remote", "SwiftUI")
    listed = client.get("/api/repositories", headers=auth_headers).json()[0]
    client.app.state.document_mutation_store.create(
        operation="create",
        repository_id=listed["id"],
        document_id=None,
        payload={"marker": "docmind-mutation:inflight"},
    )

    refused = _delete(client, auth_headers, listed["id"])

    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "REPOSITORY_BUSY"
    assert client.app.state.repository_store.get(listed["id"]) is not None


def test_delete_repository_prunes_session_and_memory_scopes(client, auth_headers) -> None:
    """会话范围与记忆范围里不能留下悬空 id。

    记忆那条尤其要紧：``session_summaries.repository_ids_json`` 是索引侧真正会读
    的输入，而 memory_chunks.repository_id 有外键。留着一个已删仓库的 id，下次
    索引就是外键约束错误 —— 一个永远重试不成功的记忆摘要。
    """
    repository_id, _ = _local_repository_with_document(client, auth_headers)
    database = client.app.state.database
    session_record = client.app.state.conversation_store.create_session(
        [repository_id, "keep"], title="会话"
    )
    with database.session() as session:
        summary = SessionSummaryRecord(
            session_id=session_record.id,
            state="ready",
            content="摘要",
            repository_ids_json=json.dumps([repository_id, "keep"]),
        )
        session.add(summary)
        session.add(
            DistillationRecord(
                title="蒸馏",
                content="内容",
                state="saved",
                repository_ids_json=json.dumps([repository_id, "keep"]),
            )
        )
        session.flush()
        session.add(
            MemoryChunkRecord(
                summary_id=summary.id,
                repository_id=repository_id,
                text="记忆",
                chunk_index=0,
                vector_id="memory-vector-1",
                indexed=True,
            )
        )

    response = _delete(client, auth_headers, repository_id)

    assert response.status_code == 204
    with database.session() as session:
        assert json.loads(session.get(SessionRecord, session_record.id).repository_scope_json) == [
            "keep"
        ]
        summary_scopes = [
            record.repository_ids_json for record in session.query(SessionSummaryRecord).all()
        ]
        distillation_scopes = [
            record.repository_ids_json for record in session.query(DistillationRecord).all()
        ]
        assert json.loads(summary_scopes[0]) == ["keep"]
        assert json.loads(distillation_scopes[0]) == ["keep"]
        assert session.query(MemoryChunkRecord).count() == 0


def test_delete_repository_cascades_document_versions_and_graph_nodes(client, auth_headers) -> None:
    """版本历史与图谱节点按 document_id 挂，没有外键，必须显式删。

    只删仓库时它们会活下来并在图谱里继续列着已删文档 —— 那正是用户以为
    "已经删干净了"的地方。
    """
    repository_id, document_id = _local_repository_with_document(client, auth_headers)
    database = client.app.state.database
    with database.session() as session:
        session.add(
            DocumentVersionRecord(
                document_id=document_id,
                version_no=1,
                title="旧标题",
                content="旧内容",
                content_sha256="0" * 64,
                created_at=datetime.now(UTC),
            )
        )
        session.add(
            GraphNodeRecord(id=document_id, kind="document", label="论文", document_id=document_id)
        )
        session.add(
            GraphEdgeRecord(source_id=document_id, target_id="concept-x", relation="mentions")
        )

    response = _delete(client, auth_headers, repository_id)

    assert response.status_code == 204
    with database.session() as session:
        assert session.query(DocumentVersionRecord).count() == 0
        assert session.query(GraphNodeRecord).count() == 0
        assert session.query(GraphEdgeRecord).count() == 0


def test_delete_repository_queues_memory_vector_cleanup_when_delete_fails(
    client, auth_headers, monkeypatch
) -> None:
    """记忆向量住在跨仓库共享的集合里，删不掉时必须有durable 记录兜住。

    与仓库集合不同，这里不能整块删 —— 共享集合里还有别的仓库的记忆。所以只有
    "逐条删 + 失败留记录"这一条路，而这条路上唯一会泄漏的失败模式就是"以为删掉了
    却没删"。断言清理记录里带着那条向量 id 与集合名。
    """
    repository_id, _ = _local_repository_with_document(client, auth_headers)
    database = client.app.state.database
    session_record = client.app.state.conversation_store.create_session([repository_id])
    with database.session() as session:
        summary = SessionSummaryRecord(
            session_id=session_record.id,
            state="ready",
            content="摘要",
            repository_ids_json=json.dumps([repository_id]),
        )
        session.add(summary)
        session.flush()
        session.add(
            MemoryChunkRecord(
                summary_id=summary.id,
                repository_id=repository_id,
                text="记忆",
                chunk_index=0,
                vector_id="memory-vector-1",
                indexed=True,
            )
        )
    vector_store = client.app.state.vector_store

    def refuse(*_args: object) -> None:
        raise RuntimeError("memory vector delete failed")

    monkeypatch.setattr(vector_store, "delete_memory", refuse)

    response = _delete(client, auth_headers, repository_id)

    assert response.status_code == 204
    pending = client.app.state.memory_store.list_vector_cleanups()
    assert len(pending) == 1
    assert pending[0].collection == "session_summaries"
    assert json.loads(pending[0].vector_ids_json) == ["memory-vector-1"]


def test_delete_repository_leaves_the_remote_untouched(
    client, auth_headers, remote_provider: FakeRemoteProvider
) -> None:
    """删除的语义是"从 DocMind 移除"，不是"把远端资料删掉"。"""
    remote_provider.seed_repository("repo-remote", "SwiftUI")
    listed = client.get("/api/repositories", headers=auth_headers).json()[0]

    response = _delete(client, auth_headers, listed["id"])

    assert response.status_code == 204
    assert remote_provider.write_calls == []


def test_delete_repository_is_not_found_for_unknown_id(client, auth_headers) -> None:
    response = _delete(client, auth_headers, "00000000-0000-4000-8000-000000000000")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_removed_remote_repository_stays_removed_across_refreshes(
    client, auth_headers, remote_provider: FakeRemoteProvider
) -> None:
    """移除远端绑定的知识库必须持久 —— 刷新列表不能把它自己带回来。

    这是这个功能的全部意义：`GET /api/repositories` 会把 provider 返回的远端仓库
    都 upsert 进来，所以"删掉一行"本身不构成移除。
    """
    remote_provider.seed_repository("repo-remote", "SwiftUI")
    listed = client.get("/api/repositories", headers=auth_headers).json()[0]

    assert _delete(client, auth_headers, listed["id"]).status_code == 204

    assert client.get("/api/repositories", headers=auth_headers).json() == []
    removed = client.get("/api/repositories/removed", headers=auth_headers).json()
    assert len(removed) == 1
    assert removed[0]["name"] == "SwiftUI"
    assert removed[0]["provider"] == "yuque"
    assert removed[0]["remoteId"] == "repo-remote"


def test_removing_the_same_remote_identity_twice_keeps_one_record(
    client, auth_headers, remote_provider: FakeRemoteProvider
) -> None:
    """同一个远端身份被移除两次，只能留下一条记录，而且不能撞唯一约束。

    正常流程走不到这里（列表同步会跳过已移除的身份），所以这条记录必须靠 upsert
    写入而不是裸 insert —— 否则一旦某条代码路径把仓库行又建了回来，用户点删除
    拿到的是一个 500，而不是又一次成功的移除。
    """
    remote_provider.seed_repository("repo-remote", "SwiftUI")
    listed = client.get("/api/repositories", headers=auth_headers).json()[0]
    assert _delete(client, auth_headers, listed["id"]).status_code == 204

    # 把同一个远端身份重新建出来（绕过列表同步的跳过），再删一次。
    recreated = client.app.state.repository_store.upsert_remote(
        provider="yuque",
        remote_id="repo-remote",
        name="SwiftUI",
        description=None,
        remote_url=None,
    )
    assert _delete(client, auth_headers, recreated.id).status_code == 204

    removed = client.get("/api/repositories/removed", headers=auth_headers).json()
    assert len(removed) == 1
    assert removed[0]["remoteId"] == "repo-remote"


def test_local_repository_removal_leaves_no_tombstone(client, auth_headers) -> None:
    """本地知识库不会被列表同步拉回来，所以不该记移除记录。

    记了的话"已移除"列表里会攒下一堆无法恢复的本地库条目 —— 恢复只对远端
    身份有意义。
    """
    repository_id, _ = _local_repository_with_document(client, auth_headers)

    assert _delete(client, auth_headers, repository_id).status_code == 204

    assert client.get("/api/repositories/removed", headers=auth_headers).json() == []


def test_restore_removed_repository_brings_it_back_without_documents(
    client, auth_headers, remote_provider: FakeRemoteProvider
) -> None:
    """恢复把条目拉回列表，文档留给用户点「立即同步」再拉。

    断言 `indexedDocumentCount == 0`：恢复不能假装文档还在，否则用户会以为
    一切照旧，而检索其实什么都搜不到。
    """
    remote_provider.seed_repository("repo-remote", "SwiftUI")
    listed = client.get("/api/repositories", headers=auth_headers).json()[0]
    assert _delete(client, auth_headers, listed["id"]).status_code == 204
    removal_id = client.get("/api/repositories/removed", headers=auth_headers).json()[0]["id"]

    restored = client.post(
        f"/api/repositories/removed/{removal_id}/restore", headers=auth_headers
    )

    assert restored.status_code == 200, restored.text
    assert restored.json()["remoteId"] == "repo-remote"
    assert restored.json()["name"] == "SwiftUI"
    assert restored.json()["indexedDocumentCount"] == 0
    assert client.get("/api/repositories/removed", headers=auth_headers).json() == []
    assert len(client.get("/api/repositories", headers=auth_headers).json()) == 1


def test_restore_clears_the_record_when_the_remote_is_gone(
    client, auth_headers, remote_provider: FakeRemoteProvider
) -> None:
    """远端已经没有了就如实报错，并把这条恢复不了的记录清掉。

    留着它只会让"已移除"列表永远挂着一个点不动的条目，而用户没有任何办法
    把它去掉。
    """
    remote_provider.seed_repository("repo-remote", "SwiftUI")
    listed = client.get("/api/repositories", headers=auth_headers).json()[0]
    assert _delete(client, auth_headers, listed["id"]).status_code == 204
    removal_id = client.get("/api/repositories/removed", headers=auth_headers).json()[0]["id"]
    remote_provider.forget_repository("repo-remote")

    refused = client.post(f"/api/repositories/removed/{removal_id}/restore", headers=auth_headers)

    assert refused.status_code == 404
    assert refused.json()["error"]["code"] == "REMOTE_NOT_FOUND"
    assert client.get("/api/repositories/removed", headers=auth_headers).json() == []


def test_restore_requires_a_configured_provider(
    client, auth_headers, remote_provider: FakeRemoteProvider
) -> None:
    """插件被停用/凭据丢失时不能假装恢复成功，记录也不能丢。"""
    remote_provider.seed_repository("repo-remote", "SwiftUI")
    listed = client.get("/api/repositories", headers=auth_headers).json()[0]
    assert _delete(client, auth_headers, listed["id"]).status_code == 204
    removal_id = client.get("/api/repositories/removed", headers=auth_headers).json()[0]["id"]
    install_remote_provider(client.app, remote_provider, configured=False)

    refused = client.post(f"/api/repositories/removed/{removal_id}/restore", headers=auth_headers)

    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "REMOTE_CREDENTIAL_REQUIRED"
    assert len(client.get("/api/repositories/removed", headers=auth_headers).json()) == 1


def test_restore_is_not_found_for_unknown_removal_id(client, auth_headers) -> None:
    response = client.post(
        "/api/repositories/removed/00000000-0000-4000-8000-000000000000/restore",
        headers=auth_headers,
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_deleting_a_repository_also_drops_its_removed_document_records(
    client, auth_headers, remote_provider: FakeRemoteProvider
) -> None:
    """删掉知识库时「已移除的文档」记录必须一起走（靠外键级联，不靠应用层记得）。

    漏掉它们的后果不是报错，而是攒下一堆指向已删知识库的孤儿行：界面不再显示，
    数据库里却永远留着。这条同时验证迁移里那个 ON DELETE CASCADE 真的建成了 ——
    ORM 层面的单测抓不到这件事。
    """
    remote_provider.seed_repository("repo-remote", "SwiftUI")
    # 必须换掉真实嵌入模型：否则这一步会去 HuggingFace 下载 bge-m3，在没网的
    # 环境里这个测试表现为"卡死"，而不是失败。
    client.app.state.embedding_provider = FakeEmbeddingProvider(
        client.app.state.settings.embedding_settings
    )
    listed = client.get("/api/repositories", headers=auth_headers).json()[0]
    created = client.post(
        f"/api/repositories/{listed['id']}/documents",
        headers=auth_headers,
        json={"title": "甲", "content": "# 甲\n\n正文"},
    )
    assert created.status_code == 201, created.text
    document_id = created.json()["id"]
    assert client.request(
        "DELETE", f"/api/documents/{document_id}", headers=auth_headers, json={"confirm": True}
    ).status_code == 204
    assert len(client.app.state.removed_document_store.list_for_repository(listed["id"])) == 1

    assert _delete(client, auth_headers, listed["id"]).status_code == 204

    with client.app.state.database.session() as session:
        assert session.query(RemovedRemoteDocumentRecord).count() == 0
