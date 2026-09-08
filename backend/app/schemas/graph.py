from __future__ import annotations

from app.schemas.common import WireModel


class GraphNode(WireModel):
    id: str
    kind: str  # "document" | "topic" | "citation" | "tag"
    label: str
    document_id: str | None


class GraphEdge(WireModel):
    source_id: str
    target_id: str
    relation: str  # "references" | "mentions" | "tags"
