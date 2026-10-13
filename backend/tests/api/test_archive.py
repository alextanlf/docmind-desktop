from __future__ import annotations

from app.core.embedding import FakeEmbeddingProvider
from app.schemas.graph import GraphEdge, GraphNode


def _local_repository(client):
    return client.app.state.repository_store.create_local(name="本地库")


def _remote_repository(client):
    """建一个远端绑定的知识库（假 provider），返回 (本地 id, gateway)。"""
    gateway = client.app.state.remote_registry.get("yuque")
    remote = gateway.seed_repository("repo-remote", "SwiftUI")
    repository = client.app.state.repository_store.upsert_remote(
        provider="yuque",
        remote_id=remote.remote_id,
        name=remote.name,
        description=None,
        remote_url=remote.url,
    )
    return repository.id, gateway


def _create_document(client, auth_headers, repository_id: str, title: str, content: str | None = None):
    client.app.state.embedding_provider = FakeEmbeddingProvider(
        client.app.state.settings.embedding_settings
    )
    created = client.post(
        f"/api/repositories/{repository_id}/documents",
        headers=auth_headers,
        json={"title": title, "content": content or f"# {title}\n\n{title} 的正文"},
    )
    assert created.status_code == 201, created.text
    return created.json()


def _archive(client, auth_headers, document_id: str):
    return client.post(f"/api/documents/{document_id}/archive", headers=auth_headers)


def _titles(client, auth_headers, repository_id: str) -> set[str]:
    listed = client.get(
        f"/api/repositories/{repository_id}/documents", headers=auth_headers
    ).json()
    return {item["title"] for item in listed}


def test_archived_document_leaves_the_list_and_the_indexed_count(client, auth_headers) -> None:
    """归档 = 从列表里消失，并且"这个库还有没有可检索内容"要跟着一起变。

    后者不是装饰：会话范围校验与对话前置检查看的就是这个数字。文档全归档的库如果
    仍被判成"有内容"，用户会在一个检索一条都返回不了的范围里提问。
    """
    repository_id = _local_repository(client).id
    _create_document(client, auth_headers, repository_id, "留下")
    archived = _create_document(client, auth_headers, repository_id, "归档")
    store = client.app.state.document_store

    assert _archive(client, auth_headers, archived["id"]).status_code == 204

    assert _titles(client, auth_headers, repository_id) == {"留下"}
    assert store.indexed_count_for_repository(repository_id) == 1
    listed = client.get("/api/documents/archived", headers=auth_headers).json()
    assert [(item["title"], item["repositoryName"]) for item in listed] == [("归档", "本地库")]

    assert (
        client.post(f"/api/documents/{archived['id']}/unarchive", headers=auth_headers).status_code
        == 204
    )

    assert client.get("/api/documents/archived", headers=auth_headers).json() == []
    assert _titles(client, auth_headers, repository_id) == {"留下", "归档"}
    assert store.indexed_count_for_repository(repository_id) == 2


def test_archive_keeps_the_content_chunks_versions_and_files(client, auth_headers) -> None:
    """归档不删任何东西 —— 恢复才是即时的。

    这一条是"归档"和"永久删除"的分界线：内容、分块、版本历史、磁盘目录一个都不能少。
    """
    repository_id = _local_repository(client).id
    document = _create_document(client, auth_headers, repository_id, "归档")
    updated = client.put(
        f"/api/documents/{document['id']}",
        headers=auth_headers,
        json={"title": "归档", "content": "# 归档\n\n改过的正文"},
    )
    assert updated.status_code == 200
    store = client.app.state.document_store
    directory = client.app.state.settings.documents_dir / document["id"]
    assert directory.exists()

    assert _archive(client, auth_headers, document["id"]).status_code == 204

    assert store.get(document["id"]) is not None
    assert store.list_chunks(document["id"]) != []
    assert client.get(f"/api/documents/{document['id']}/versions", headers=auth_headers).json()
    assert directory.exists()
    # 按 id 直接取详情仍然可用：归档是从"浏览与检索"里消失，不是把数据锁起来。
    assert (
        client.get(f"/api/documents/{document['id']}", headers=auth_headers).status_code == 200
    )


def test_permanent_delete_of_an_archived_document_takes_everything(client, auth_headers) -> None:
    """设置页里的永久删除：归档之后再删，必须连版本历史与磁盘目录一起带走。"""
    repository_id = _local_repository(client).id
    document = _create_document(client, auth_headers, repository_id, "归档")
    assert (
        client.put(
            f"/api/documents/{document['id']}",
            headers=auth_headers,
            json={"title": "归档", "content": "# 归档\n\n改过的正文"},
        ).status_code
        == 200
    )
    assert _archive(client, auth_headers, document["id"]).status_code == 204

    response = client.request(
        "DELETE",
        f"/api/documents/{document['id']}",
        headers=auth_headers,
        json={"confirm": True},
    )

    assert response.status_code == 204
    store = client.app.state.document_store
    assert store.get(document["id"]) is None
    assert store.list_chunks(document["id"]) == []
    assert client.get(f"/api/documents/{document['id']}/versions", headers=auth_headers).json() == []
    assert not (client.app.state.settings.documents_dir / document["id"]).exists()
    assert client.get("/api/documents/archived", headers=auth_headers).json() == []


def test_archiving_a_remote_document_records_it_so_sync_skips_it(client, auth_headers) -> None:
    """远端文档归档要写一条移出记录，取消归档要把它删掉。

    同步的判重依据是"上次见到的远端状态"，不是"本地还有没有这篇文档"。不写记录的话，
    远端下一次更新就会把归档的文档重新拉回来（实测：远端未变时不回来，一变就回来）。

    同时断言**不动**同步快照：文档行没被删过，那份快照仍然准确地描述着"上次见到的
    远端状态"，留着它下次同步才能判断出远端改过没改过；清掉会让它被当成新文档重新
    下载并重新索引一遍。
    """
    repository_id, _gateway = _remote_repository(client)
    document = _create_document(client, auth_headers, repository_id, "远端文档")
    remote_id = client.app.state.document_store.get(document["id"]).remote_id
    assert remote_id
    client.app.state.sync_state_store.upsert(
        repository_id, remote_id, "远端文档", "hash-1", "https://remote.local/d1", []
    )

    assert _archive(client, auth_headers, document["id"]).status_code == 204
    assert client.app.state.removed_document_store.identities(repository_id) == {remote_id}

    assert (
        client.post(f"/api/documents/{document['id']}/unarchive", headers=auth_headers).status_code
        == 204
    )
    assert client.app.state.removed_document_store.identities(repository_id) == set()
    assert remote_id in client.app.state.sync_state_store.get_snapshot(repository_id)


def test_batch_archive_archives_the_selection_and_ignores_foreign_ids(client, auth_headers) -> None:
    """批量归档走另一条循环：只归档这个库里的 id，跨库/已不存在的 id 直接跳过。"""
    repository_id = _local_repository(client).id
    other_repository = client.app.state.repository_store.create_local(name="另一个库")
    first = _create_document(client, auth_headers, repository_id, "甲")
    second = _create_document(client, auth_headers, repository_id, "乙")
    foreign = _create_document(client, auth_headers, other_repository.id, "别处的")

    response = client.post(
        f"/api/repositories/{repository_id}/documents/archive",
        headers=auth_headers,
        json={"documentIds": [first["id"], second["id"], foreign["id"], "not-a-document"]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["archived"] == 2
    assert _titles(client, auth_headers, repository_id) == set()
    assert _titles(client, auth_headers, other_repository.id) == {"别处的"}


def test_graph_hides_archived_document_nodes_but_keeps_shared_topics(client, auth_headers) -> None:
    """图谱也是可见面。

    文档节点（``id == document_id``）跟着文档一起隐去；共享主题节点留着 —— 另一篇
    文档还在提它 —— 但相邻关系里不能再出现归档的那一篇。
    """
    repository_id = _local_repository(client).id
    first = _create_document(client, auth_headers, repository_id, "第一篇")
    second = _create_document(client, auth_headers, repository_id, "第二篇")
    graph = client.app.state.graph_store
    for document in (first, second):
        graph.replace_document_graph(
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

    assert _archive(client, auth_headers, first["id"]).status_code == 204

    nodes = {node["id"] for node in client.get("/api/graph/nodes", headers=auth_headers).json()}
    assert nodes == {second["id"], "topic:引言"}
    edges = client.get("/api/graph/nodes/topic:引言/adjacency", headers=auth_headers).json()
    assert [edge["sourceId"] for edge in edges] == [second["id"]]


def test_legacy_removal_records_are_listed_globally_and_never_double_listed(
    client, auth_headers
) -> None:
    """旧流程留下的移除记录要能跨库列出来，而归档的文档不能同时出现在两份列表里。

    同一张 ``removed_remote_documents`` 表现在装了两件事：归档一篇远端文档也会写一条
    （作用是让同步跳过它），永久移除写的记录才是"本地已经没有了"。两者对用户的含义
    不同 —— 一个随时可恢复、一个要重新下载 —— 混在一起用户只能猜哪个是真的。
    """
    repository_id, _gateway = _remote_repository(client)
    archived = _create_document(client, auth_headers, repository_id, "归档的")
    purged = _create_document(client, auth_headers, repository_id, "删掉的")
    assert _archive(client, auth_headers, archived["id"]).status_code == 204
    assert (
        client.request(
            "DELETE",
            f"/api/documents/{purged['id']}",
            headers=auth_headers,
            json={"confirm": True},
        ).status_code
        == 204
    )

    listed = client.get("/api/removed-documents", headers=auth_headers).json()

    assert [item["title"] for item in listed] == ["删掉的"]
    assert listed[0]["repositoryName"] == "SwiftUI"
    # 归档的那篇在它该在的地方。
    archived_list = client.get("/api/documents/archived", headers=auth_headers).json()
    assert [item["title"] for item in archived_list] == ["归档的"]
