from __future__ import annotations

import re

from app.schemas.graph import GraphEdge, GraphNode

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_CITATION_RE = re.compile(r"\[([SMW]\d+)\]")
_TAG_RE = re.compile(r"(?:^|\s)#([\w\u4e00-\u9fff-]+)")


def extract_graph(
    document_id: str, title: str, content: str
) -> tuple[list[GraphNode], list[GraphEdge]]:
    nodes: list[GraphNode] = [
        GraphNode(id=document_id, kind="document", label=title, document_id=document_id)
    ]
    edges: list[GraphEdge] = []
    seen: set[str] = {document_id}

    for match in _HEADING_RE.finditer(content):
        label = match.group(2).strip()
        node_id = f"topic:{label}"
        if node_id not in seen:
            seen.add(node_id)
            nodes.append(
                GraphNode(id=node_id, kind="topic", label=label, document_id=document_id)
            )
            edges.append(
                GraphEdge(source_id=document_id, target_id=node_id, relation="mentions")
            )

    for match in _CITATION_RE.finditer(content):
        label = match.group(1)
        node_id = f"citation:{label}"
        if node_id not in seen:
            seen.add(node_id)
            nodes.append(
                GraphNode(id=node_id, kind="citation", label=label, document_id=document_id)
            )
            edges.append(
                GraphEdge(source_id=document_id, target_id=node_id, relation="references")
            )

    for match in _TAG_RE.finditer(content):
        label = match.group(1)
        node_id = f"tag:{label}"
        if node_id not in seen:
            seen.add(node_id)
            nodes.append(
                GraphNode(id=node_id, kind="tag", label=label, document_id=document_id)
            )
            edges.append(
                GraphEdge(source_id=document_id, target_id=node_id, relation="tags")
            )

    return nodes, edges
