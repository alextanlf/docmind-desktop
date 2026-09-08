from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/repositories", tags=["sync"])


@router.post("/{repository_id}/sync")
async def sync_repository(request: Request, repository_id: str) -> dict[str, object]:
    outcome = await request.app.state.sync_service.sync_repository(repository_id)
    return outcome.model_dump()


@router.get("/{repository_id}/sync")
async def sync_status(request: Request, repository_id: str) -> dict[str, object]:
    last_synced_at = request.app.state.sync_state_store.last_synced_at(repository_id)
    return {"last_synced_at": last_synced_at}
