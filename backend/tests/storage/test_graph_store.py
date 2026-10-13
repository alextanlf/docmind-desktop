from __future__ import annotations

from app.schemas.graph import GraphEdge, GraphNode
from app.storage.database import Database
from app.storage.repositories import GraphStore


def _write_document(store: GraphStore, document_id: str, title: str, topics: list[str]) -> None:
    """按 extractor 的真实形状写一张图：文档节点 + 主题节点 + mentions 边。"""
    nodes = [
        GraphNode(id=document_id, kind="document", label=title, document_id=document_id),
        *[
            GraphNode(id=f"topic:{topic}", kind="topic", label=topic, document_id=document_id)
            for topic in topics
        ],
    ]
    edges = [
        GraphEdge(source_id=document_id, target_id=f"topic:{topic}", relation="mentions")
        for topic in topics
    ]
    store.replace_document_graph(document_id, nodes, edges)


def test_two_documents_may_share_a_topic_node(database: Database) -> None:
    """同一个标题在两篇文档里是同一个节点 —— 原先第二次写回直接撞主键。

    撞主键的后果不是"少一个节点"：整个 ``replace_document_graph`` 回滚，第二篇文档
    在图里完全没有痕迹（同步路径上正文这时已经落库了，于是表现为"同步失败"）。
    """
    store = GraphStore(database)

    _write_document(store, "doc-1", "第一篇", ["引言", "方法"])
    _write_document(store, "doc-2", "第二篇", ["引言"])

    nodes = {node.id: node for node in store.list_nodes()}
    assert set(nodes) == {"doc-1", "doc-2", "topic:引言", "topic:方法"}
    # 共享节点不记主人：带上 document_id 就是一条会撒谎的归属信息。
    assert nodes["topic:引言"].document_id is None
    assert nodes["doc-1"].document_id == "doc-1"
    assert sorted(edge.source_id for edge in store.adjacency("topic:引言")) == ["doc-1", "doc-2"]


def test_rewriting_a_document_drops_the_topics_it_no_longer_mentions(database: Database) -> None:
    """重同步换了标题之后，没人再引用的旧主题节点不该留在图里。"""
    store = GraphStore(database)

    _write_document(store, "doc-1", "第一篇", ["旧标题"])
    _write_document(store, "doc-1", "第一篇", ["新标题"])

    assert {node.id for node in store.list_nodes()} == {"doc-1", "topic:新标题"}


def test_deleting_one_document_keeps_the_shared_node_for_the_others(database: Database) -> None:
    """删一篇文档只该带走它自己的节点，以及因此没人再用的共享节点。"""
    store = GraphStore(database)
    _write_document(store, "doc-1", "第一篇", ["引言", "只有它提到"])
    _write_document(store, "doc-2", "第二篇", ["引言"])

    store.delete_document_graph("doc-1")

    nodes = {node.id for node in store.list_nodes()}
    assert nodes == {"doc-2", "topic:引言"}
    assert [edge.source_id for edge in store.adjacency("topic:引言")] == ["doc-2"]

    store.delete_document_graph("doc-2")

    assert store.list_nodes() == []
    assert store.adjacency("topic:引言") == []
