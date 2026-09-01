from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request, status

from app.schemas.embedding import ModelStatus

router = APIRouter(prefix="/api/embedding", tags=["embedding"])


def _consume_terminal_exception(task: asyncio.Task[ModelStatus]) -> None:
    if not task.cancelled():
        task.exception()


@router.get("/status", response_model=ModelStatus)
async def embedding_status(request: Request) -> ModelStatus:
    return request.app.state.embedding_provider.status


@router.post("/prepare", response_model=ModelStatus, status_code=status.HTTP_202_ACCEPTED)
async def prepare_embedding(request: Request) -> ModelStatus:
    task = getattr(request.app.state, "embedding_prepare_task", None)
    if task is None or (task.done() and request.app.state.embedding_provider.status.state == "error"):
        task = asyncio.create_task(request.app.state.embedding_provider.ensure_ready())
        task.add_done_callback(_consume_terminal_exception)
        request.app.state.embedding_prepare_task = task
        # Start the task before responding so status and test providers are deterministic.
        await asyncio.sleep(0)
    return request.app.state.embedding_provider.status
