from __future__ import annotations

import asyncio
import json
from typing import Annotated, cast

from fastapi import APIRouter, Header, Request, status
from fastapi.responses import StreamingResponse

from app.api.errors import DomainError
from app.config import AppSettings
from app.document.formats import FormatRegistry, size_limit_for
from app.imports.service import ImportService
from app.schemas.imports import (
    ImportCreateRequest,
    ImportJobView,
    SourceFormatView,
    SourcePreview,
    SourceRef,
)

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


@router.get("/formats", response_model=list[SourceFormatView])
async def list_source_formats(request: Request) -> list[SourceFormatView]:
    """The formats a user may pick as a single file.

    The client builds its file dialog from this — filters, size ceiling and the
    label on the button — so a format plugin widens what can be imported
    without a matching edit in the renderer or the main process.
    """
    registry: FormatRegistry = request.app.state.format_registry
    settings: AppSettings = request.app.state.settings
    return [
        SourceFormatView(
            name=format.name,
            label=format.label or format.name,
            extensions=list(format.extensions),
            media_type=format.media_type,
            max_bytes=size_limit_for(
                format,
                binary_max_bytes=settings.pdf_max_bytes,
                text_max_bytes=settings.html_markdown_max_bytes,
            ),
        )
        for format in registry.formats()
        if format.single_file
    ]


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

    await service.ensure_terminal_event(job_id, job)

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
