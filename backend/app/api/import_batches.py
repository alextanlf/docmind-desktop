from __future__ import annotations

import asyncio
import json
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Header, Request, status
from fastapi.responses import StreamingResponse

from app.api.errors import DomainError
from app.imports.batch_service import BatchService
from app.schemas.batches import (
    BatchImportView,
    BatchItemPage,
    ConfirmBatchInput,
    CreateBatchRequest,
    RetryBatchInput,
)
from app.storage.models import BatchImportRecord

router = APIRouter(prefix="/api/import-batches", tags=["import-batches"])


def _service(request: Request) -> BatchService:
    return cast(BatchService, request.app.state.batch_service)


def _view(batch: BatchImportRecord) -> BatchImportView:
    return BatchImportView.model_validate(
        {
            "id": batch.id,
            "source_kind": batch.source_kind,
            "repository_id": batch.repository_id,
            "state": batch.state,
            "discovery_version": batch.discovery_version,
            "total_count": batch.total_count,
            "selected_count": batch.selected_count,
            "completed_count": batch.completed_count,
            "failed_count": batch.failed_count,
            "skipped_count": batch.skipped_count,
            "progress": batch.progress,
            "message": batch.message,
            "error_code": batch.error_code,
            "error_message": batch.error_message,
            "retryable": batch.retryable,
            "cancel_requested": batch.cancel_requested,
            "created_at": batch.created_at,
            "started_at": batch.started_at,
            "completed_at": batch.completed_at,
            "updated_at": batch.updated_at,
            "last_event_sequence": batch.last_event_sequence,
        }
    )


def _schedule(request: Request, coro) -> None:  # type: ignore[no-untyped-def]
    task = asyncio.create_task(coro)
    tasks: set[asyncio.Task[None]] = request.app.state.import_tasks
    tasks.add(task)
    def finish(done: asyncio.Task[None]) -> None:
        tasks.discard(done)
        if not done.cancelled():
            done.exception()

    task.add_done_callback(finish)


@router.post("", response_model=BatchImportView, status_code=status.HTTP_201_CREATED)
async def create_batch(request: Request, body: CreateBatchRequest) -> BatchImportView:
    service = _service(request)
    batch = service.create_batch(body)
    _schedule(request, service.discover_batch(batch.id))
    return _view(batch)


@router.get("", response_model=list[BatchImportView])
async def list_batches(request: Request) -> list[BatchImportView]:
    return [_view(batch) for batch in _service(request).list_batches()]


@router.get("/{batch_id}", response_model=BatchImportView)
async def get_batch(batch_id: UUID, request: Request) -> BatchImportView:
    return _view(_service(request).get(str(batch_id)))


@router.get("/{batch_id}/items", response_model=BatchItemPage)
async def list_batch_items(
    batch_id: UUID,
    request: Request,
    cursor: str | None = None,
    limit: int = 100,
    state: str | None = None,
    selected: bool | None = None,
) -> BatchItemPage:
    return _service(request).list_items(
        str(batch_id), cursor=cursor, limit=limit, state=state, selected=selected
    )


@router.post("/{batch_id}/confirm", response_model=BatchImportView)
async def confirm_batch(batch_id: UUID, body: ConfirmBatchInput, request: Request) -> BatchImportView:
    batch = await _service(request).confirm(str(batch_id), body)
    _schedule(request, _service(request).continue_batch(str(batch_id)))
    return _view(batch)


@router.post("/{batch_id}/cancel", response_model=BatchImportView)
async def cancel_batch(batch_id: UUID, request: Request) -> BatchImportView:
    return _view(await _service(request).cancel_batch(str(batch_id)))


@router.post("/{batch_id}/continue", response_model=BatchImportView)
async def continue_batch(batch_id: UUID, request: Request) -> BatchImportView:
    service = _service(request)
    batch = await service.continue_batch(str(batch_id), wait=False)
    _schedule(request, service.continue_batch(str(batch_id)))
    return _view(batch)


@router.post("/{batch_id}/retry", response_model=BatchImportView)
async def retry_batch_item_body(batch_id: UUID, request: Request, body: RetryBatchInput | None = None) -> BatchImportView:
    item_ids = None if body is None or not body.item_ids else [str(item_id) for item_id in body.item_ids]
    service = _service(request)
    batch = await service.retry_items(str(batch_id), item_ids, wait=False)
    _schedule(request, service.continue_batch(str(batch_id)))
    return _view(batch)


@router.get("/{batch_id}/events")
async def batch_events(
    batch_id: UUID,
    request: Request,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    service = _service(request)
    service.get(str(batch_id))
    try:
        after = int(last_event_id) if last_event_id is not None else 0
        if after < 0:
            raise ValueError
    except ValueError:
        raise DomainError("IMPORT_EVENT_CURSOR_INVALID", "导入事件序号无效", 400) from None
    await service.ensure_terminal_event(str(batch_id))

    async def stream():
        if service.event_broker is None:
            return
        async for event in service.event_broker.subscribe(str(batch_id), after):
            payload = json.dumps(
                event.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
            )
            yield f"id: {event.sequence}\ndata: {payload}\n\n"
    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
