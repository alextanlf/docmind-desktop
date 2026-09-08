from __future__ import annotations

from app.schemas.graph import GraphEdge, GraphNode
from app.storage.database import Database
from app.storage.repositories import GraphStore


def test_replace_document_graph_removes_stale_edges(database: Database) -> None:
    store = GraphStore(database)
    store.replace_document_graph(
        "doc-1",
        [GraphNode(id="n1", kind="citation", label="S1", document_id="doc-1")],
        [GraphEdge(source_id="doc-1", target_id="n1", relation="references")],
    )

    assert len(store.adjacency("doc-1")) == 1

    store.replace_document_graph(
        "doc-1",
        [GraphNode(id="n2", kind="tag", label="性能", document_id="doc-1")],
        [],
    )

    assert store.adjacency("doc-1") == []
