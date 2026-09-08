from __future__ import annotations

from app.graph.extractor import extract_graph


def test_extract_graph_finds_headings_citations_and_tags() -> None:
    content = "# SwiftUI\n\n状态管理见 [S1]。标签：\n- #性能\n"

    nodes, edges = extract_graph("doc-1", "State", content)

    kinds = {node.kind for node in nodes}
    assert {"document", "topic", "citation", "tag"} <= kinds
    assert any(edge.relation == "references" for edge in edges)
    assert any(edge.relation == "tags" for edge in edges)
