from __future__ import annotations

from app.schemas.graph import GraphNode


def test_graph_nodes_endpoint(client, auth_headers) -> None:
    client.app.state.graph_store.replace_document_graph(
        "doc-1",
        [GraphNode(id="n1", kind="citation", label="S1", document_id="doc-1")],
        [],
    )

    response = client.get("/api/graph/nodes", headers=auth_headers)

    assert response.status_code == 200
    assert [node["label"] for node in response.json()] == ["S1"]
