from __future__ import annotations

import asyncio
import json
from typing import Annotated, cast

from fastapi import APIRouter, Header, Request, status
from fastapi.responses import StreamingResponse

from app.api.errors import DomainError
from app.imports.service import ImportService
from app.schemas.imports import ImportCreateRequest, ImportJobView, SourcePreview, SourceRef

router = APIRouter(prefix="/api/imports", tags=["imports"])


def _service(request: Request) -> ImportService:
    return cast(ImportService, request.app.state.import_service)


def _schedule(request: Request, job_id: str) -> None:
    task = asyncio.create_task(_service(request).run(job_id))
    tasks: set[asyncio.Task[None]] = request.app.state.import_tasks
    tasks.add(task)

    def finish(completed: asyncio.Task[None]) -> None:
        tasks.discard(completed)
        if not completed.cancelled():
            completed.exception()

    task.add_done_callback(finish)


@router.post("/inspect", response_model=SourcePreview)
async def inspect_source(request: Request, ref: SourceRef) -> SourcePreview:
    return await _service(request).inspect(ref)


@router.post("", response_model=ImportJobView, status_code=status.HTTP_202_ACCEPTED)
async def create_import(request: Request, body: ImportCreateRequest) -> ImportJobView:
    job = await _service(request).create(body)
    _schedule(request, job.id)
    return job


@router.get("/{job_id}", response_model=ImportJobView)
async def get_import(request: Request, job_id: str) -> ImportJobView:
    return _service(request).get(job_id)


@router.post(
    "/{job_id}/retry", response_model=ImportJobView, status_code=status.HTTP_202_ACCEPTED
)
async def retry_import(request: Request, job_id: str) -> ImportJobView:
    job = await _service(request).retry(job_id)
    _schedule(request, job.id)
    return job


@router.post("/{job_id}/cancel", response_model=ImportJobView)
async def cancel_import(request: Request, job_id: str) -> ImportJobView:
    return await _service(request).cancel(job_id)


@router.get("/{job_id}/events")
async def import_events(
    request: Request,
    job_id: str,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    service = _service(request)
    job = service.get(job_id)
    try:
        after_sequence = int(last_event_id) if last_event_id is not None else 0
        if after_sequence < 0:
            raise ValueError
    except ValueError:
        raise DomainError("IMPORT_EVENT_CURSOR_INVALID", "导入事件序号无效", 400) from None

    if job.state == "failed":
        await service.event_broker.publish(
            job_id,
            "error",
            {
                "progress": job.progress,
                "state": job.state,
                "code": job.error_code,
                "message": job.error_message or job.message,
                "retryable": job.retryable,
            },
        )
    elif job.state in {"completed", "cancelled"}:
        await service.event_broker.publish(
            job_id,
            "done",
            {"progress": job.progress, "state": job.state, "message": job.message},
        )

    async def stream():  # type: ignore[no-untyped-def]
        async for event in service.event_broker.subscribe(job_id, after_sequence):
            data = json.dumps(
                event.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            yield f"id: {event.sequence}\ndata: {data}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
