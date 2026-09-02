from __future__ import annotations

import asyncio
import json
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Header, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.errors import DomainError
from app.document.discovery import DirectoryDiscovery
from app.imports.batch_service import BatchService
from app.schemas.batches import (
    BatchImportView,
    BatchItemPage,
    ConfirmBatchInput,
    CreateBatchRequest,
    DiscoveryRequest,
    StagedDirectoryBatchRequest,
)
from app.storage.models import BatchImportRecord, BatchItemRecord, BatchState
from app.storage.repositories import BatchImportStore

router = APIRouter(prefix="/api/import-batches", tags=["import-batches"])


def _service(request: Request) -> BatchService:
    return cast(BatchService, request.app.state.batch_service)


def _store(request: Request) -> BatchImportStore:
    return cast(BatchImportStore, request.app.state.batch_store)


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
    if not isinstance(body, StagedDirectoryBatchRequest):
        raise DomainError("INVALID_REQUEST", "当前仅支持 staged_directory 来源", 422)
    batch = BatchImportRecord(
        source_kind=body.kind,
        source_descriptor_json=json.dumps(
            {"collectionId": body.source_id}, separators=(",", ":")
        ),
        repository_id=str(body.repository_id),
        state=BatchState.DISCOVERING,
        message="正在发现目录",
    )
    _store(request).create_batch(batch)
    _schedule(request, _discover(request, batch.id))
    return _view(batch)


async def _discover(request: Request, batch_id: str) -> None:
    service = _service(request)
    store = _store(request)
    batch = service.get(batch_id)
    descriptor = json.loads(batch.source_descriptor_json)
    discovery = DirectoryDiscovery(
        request.app.state.settings.staging_dir,
        request.app.state.settings.staging_manifest_max_bytes,
    )

    async def emit(progress) -> None:  # type: ignore[no-untyped-def]
        await service._publish(batch_id, "progress", progress.model_dump(mode="json"))

    try:
        result = await discovery.discover(
            DiscoveryRequest(
                batch_id=UUID(batch_id),
                source_kind="staged_directory",
                source_descriptor=descriptor,
                repository_id=UUID(batch.repository_id) if batch.repository_id else None,
            ), emit,
        )
        if len(result.sources) > request.app.state.settings.batch_max_items:
            raise DomainError("BATCH_LIMIT_EXCEEDED", "目录文件数量超过限制", 413)
        items = [
            BatchItemRecord(
                source_identity=source.source_identity,
                source_revision=source.source_revision,
                title=source.title,
                display_path=source.display_path,
                media_type=source.media_type,
                size_bytes=source.size_bytes,
                cached_source_json=source.cached_source.model_dump(mode="json"),
                remote_binding_json=(
                    source.remote_binding.model_dump(mode="json")
                    if source.remote_binding
                    else None
                ),
                allowed_actions_json=["create", "skip"],
            )
            for source in result.sources
        ]
        store.insert_discovered_items(batch_id, items)
        store.set_state(batch_id, BatchState.AWAITING_CONFIRMATION, message="目录发现完成")
    except asyncio.CancelledError:
        current = store.get(batch_id)
        if current is not None and current.state == BatchState.DISCOVERING:
            with store.database.session() as session:
                persisted = session.get(BatchImportRecord, batch_id)
                if persisted is not None:
                    persisted.state = BatchState.FAILED
                    persisted.error_code = "BATCH_APP_RESTARTED"
                    persisted.error_message = "应用重启导致目录发现中断"
                    persisted.retryable = True
                    persisted.message = persisted.error_message
        raise
    except Exception as error:  # noqa: BLE001
        current = store.get(batch_id)
        if current is not None and current.state == BatchState.DISCOVERING:
            code = getattr(error, "code", "BATCH_APP_RESTARTED")
            message = getattr(error, "message", None) or "目录发现失败"
            with store.database.session() as session:
                persisted = session.get(BatchImportRecord, batch_id)
                if persisted is not None:
                    persisted.state = BatchState.FAILED
                    persisted.error_code = code
                    persisted.error_message = message
                    persisted.retryable = False
                    persisted.message = message


@router.get("", response_model=list[BatchImportView])
async def list_batches(request: Request) -> list[BatchImportView]:
    with _store(request).database.session() as session:
        return [_view(batch) for batch in session.scalars(select(BatchImportRecord).order_by(BatchImportRecord.created_at.desc()))]


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
    _service(request).get(str(batch_id))
    try:
        return _store(request).list_items(
            str(batch_id), cursor=cursor, limit=limit, state=state, selected=selected
        )
    except ValueError:
        raise DomainError("INVALID_REQUEST", "分页参数无效", 422) from None


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
    return _view(await _service(request).continue_batch(str(batch_id)))


@router.post("/{batch_id}/retry/{item_id}", response_model=BatchImportView)
async def retry_batch_item(batch_id: UUID, item_id: UUID, request: Request) -> BatchImportView:
    return _view(await _service(request).retry_item(str(batch_id), str(item_id)))


@router.post("/{batch_id}/retry", response_model=BatchImportView)
async def retry_batch_item_body(batch_id: UUID, request: Request, item_id: UUID) -> BatchImportView:
    return _view(await _service(request).retry_item(str(batch_id), str(item_id)))


@router.post("/{batch_id}/items/{item_id}/retry", response_model=BatchImportView)
async def retry_batch_item_nested(batch_id: UUID, item_id: UUID, request: Request) -> BatchImportView:
    return _view(await _service(request).retry_item(str(batch_id), str(item_id)))


@router.get("/{batch_id}/events")
async def batch_events(
    batch_id: UUID,
    request: Request,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    _service(request).get(str(batch_id))
    try:
        after = int(last_event_id) if last_event_id is not None else 0
        if after < 0:
            raise ValueError
    except ValueError:
        raise DomainError("IMPORT_EVENT_CURSOR_INVALID", "导入事件序号无效", 400) from None
    async def stream():
        async for event in _service(request).event_broker.subscribe(str(batch_id), after):
            payload = json.dumps(
                event.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
            )
            yield f"id: {event.sequence}\ndata: {payload}\n\n"
    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
