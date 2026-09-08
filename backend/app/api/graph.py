from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("/nodes")
async def list_nodes(request: Request) -> list[dict[str, object]]:
    return [node.model_dump() for node in request.app.state.graph_store.list_nodes()]


@router.get("/nodes/{node_id}/adjacency")
async def node_adjacency(request: Request, node_id: str) -> list[dict[str, object]]:
    return [edge.model_dump() for edge in request.app.state.graph_store.adjacency(node_id)]
