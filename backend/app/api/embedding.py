from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request, status

from app.schemas.embedding import ModelStatus

router = APIRouter(prefix="/api/embedding", tags=["embedding"])


def consume_terminal_exception(task: asyncio.Task[ModelStatus]) -> None:
    """取出 task 的终态异常，避免 asyncio 报 "exception was never retrieved"。"""

    if not task.cancelled():
        task.exception()


@router.get("/status", response_model=ModelStatus)
async def embedding_status(request: Request) -> ModelStatus:
    return request.app.state.embedding_provider.status


@router.post("/prepare", response_model=ModelStatus, status_code=status.HTTP_202_ACCEPTED)
async def prepare_embedding(request: Request) -> ModelStatus:
    """重试加载嵌入模型。

    正常路径不需要调用：应用启动时 lifespan 已自动预热（见 main.py），
    且任何索引请求都会经require_ready_embedding() 兜底ensure_ready()。
    这里只保留给「上次加载失败、用户修好环境后想立刻重试」这一种情况。
    """
    task = getattr(request.app.state, "embedding_prepare_task", None)
    if task is None or (task.done() and request.app.state.embedding_provider.status.state == "error"):
        task = asyncio.create_task(request.app.state.embedding_provider.ensure_ready())
        task.add_done_callback(consume_terminal_exception)
        request.app.state.embedding_prepare_task = task
        await asyncio.sleep(0)
    if not task.done():
        await task
    return request.app.state.embedding_provider.status
