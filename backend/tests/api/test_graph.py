from __future__ import annotations

from app.schemas.graph import GraphEdge, GraphNode


def test_graph_nodes_endpoint_lists_a_shared_topic_once_with_both_owners(
    client, auth_headers
) -> None:
    """两篇文档写同一个标题时节点只有一份，而且两篇都在它的相邻关系里。

    原先第二次写回直接撞 ``graph_nodes.id`` 主键，所以这条断言在修好之前根本拿不到
    数据。根节点的 ``documentId`` 是它自己，共享节点一律为 null —— 它不属于任何一篇。
    """
    store = client.app.state.graph_store
    for document_id, label in (("doc-1", "第一篇"), ("doc-2", "第二篇")):
        store.replace_document_graph(
            document_id,
            [
                GraphNode(id=document_id, kind="document", label=label, document_id=document_id),
                GraphNode(id="topic:引言", kind="topic", label="引言", document_id=document_id),
            ],
            [GraphEdge(source_id=document_id, target_id="topic:引言", relation="mentions")],
        )

    response = client.get("/api/graph/nodes", headers=auth_headers)

    assert response.status_code == 200
    assert {node["label"]: node["documentId"] for node in response.json()} == {
        "引言": None,
        "第一篇": "doc-1",
        "第二篇": "doc-2",
    }
    edges = client.get("/api/graph/nodes/topic:引言/adjacency", headers=auth_headers).json()
    assert sorted(edge["sourceId"] for edge in edges) == ["doc-1", "doc-2"]
