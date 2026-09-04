from __future__ import annotations

import base64
import json
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.errors import DomainError
from app.schemas.memory import (
    DistillationEdit,
    DistillationTarget,
    DistillationView,
    MemoryItemPage,
    MemoryItemView,
    SessionMemorySummaryView,
)
from app.storage.models import (
    DistillationRecord,
    MemoryChunkRecord,
    MemoryVectorCleanupRecord,
    SessionSummaryRecord,
)

router = APIRouter(prefix="/api")


def _summary_view(record: SessionSummaryRecord) -> SessionMemorySummaryView:
    return SessionMemorySummaryView(id=record.id, session_id=record.session_id, state=record.state, content=record.content, topics=json.loads(record.topics_json or "[]"), repository_ids=json.loads(record.repository_ids_json or "[]"), error_code=record.error_code, retryable=record.retryable)


def _distillation_view(record: DistillationRecord) -> DistillationView:
    return DistillationView(id=record.id, session_id=record.session_id, title=record.title, content=record.content, key_points=json.loads(record.key_points_json or "[]"), sources=json.loads(record.sources_json or "[]"), repository_ids=json.loads(record.repository_ids_json or "[]"), state=record.state, storage_target=record.target, local_path=record.local_path, document_id=record.document_id, yuque_url=record.remote_url, error_code=record.error_code, retryable=record.retryable, created_at=record.created_at, updated_at=record.updated_at)


@router.get("/sessions/{session_id}/summary", response_model=SessionMemorySummaryView | None)
def get_summary(session_id: UUID, request: Request):
    record = request.app.state.memory_store.get_summary(str(session_id))
    return _summary_view(record) if record else None


@router.post("/sessions/{session_id}/summary/regenerate", response_model=SessionMemorySummaryView)
async def regenerate_summary(session_id: UUID, request: Request):
    return _summary_view(await request.app.state.summary_service.regenerate(str(session_id)))


@router.delete("/sessions/{session_id}/summary", status_code=status.HTTP_204_NO_CONTENT)
def delete_summary(session_id: UUID, request: Request):
    with request.app.state.database.session() as db:
        record = db.scalar(select(SessionSummaryRecord).where(SessionSummaryRecord.session_id == str(session_id)))
        if record is not None:
            vector_ids = list(db.scalars(select(MemoryChunkRecord.vector_id).where(MemoryChunkRecord.summary_id == record.id)))
            if vector_ids:
                db.add(MemoryVectorCleanupRecord(collection="_session_summaries", vector_ids_json=json.dumps(vector_ids)))
            db.delete(record)
    return Response(status_code=204)


@router.post("/sessions/{session_id}/distillations", response_model=DistillationView)
async def create_distillation(session_id: UUID, request: Request):
    return _distillation_view(await request.app.state.distillation_service.create(str(session_id)))


@router.get("/distillations/{distillation_id}", response_model=DistillationView)
def get_distillation(distillation_id: UUID, request: Request):
    return _distillation_view(request.app.state.distillation_service.get(str(distillation_id)))


@router.put("/distillations/{distillation_id}", response_model=DistillationView)
def update_distillation(distillation_id: UUID, body: DistillationEdit, request: Request):
    return _distillation_view(request.app.state.distillation_service.update(str(distillation_id), body))


@router.post("/distillations/{distillation_id}/regenerate", response_model=DistillationView)
async def regenerate_distillation(distillation_id: UUID, request: Request):
    return _distillation_view(await request.app.state.distillation_service.regenerate(str(distillation_id)))


@router.post("/distillations/{distillation_id}/save", response_model=DistillationView)
async def save_distillation(distillation_id: UUID, body: DistillationTarget, request: Request):
    return _distillation_view(await request.app.state.distillation_service.save(str(distillation_id), body))


@router.delete("/distillations/{distillation_id}", status_code=204)
def delete_distillation(distillation_id: UUID, request: Request):
    request.app.state.distillation_service.delete(str(distillation_id))


@router.get("/distillations/{distillation_id}/events")
async def distillation_events(distillation_id: UUID, request: Request):
    record = request.app.state.distillation_service.get(str(distillation_id))
    view = _distillation_view(record)
    try:
        after_sequence = int(request.headers.get("Last-Event-ID", "0"))
        if after_sequence < 0:
            raise ValueError
    except ValueError as error:
        raise DomainError("INVALID_CURSOR", "事件游标无效", 400) from error
    sequence = record.last_event_sequence
    event_type = "error" if record.state == "failed" else "done"
    payload = json.dumps({"request_id": str(distillation_id), "type": event_type, "sequence": sequence, "payload": view.model_dump(mode="json", by_alias=True)}, ensure_ascii=False)
    async def stream():
        terminal = await request.app.state.distillation_event_broker.terminal(str(distillation_id))
        if terminal is None and record.state in {"draft", "saved", "saved_unindexed", "failed"} and after_sequence < sequence:
            yield f"id: {sequence}\ndata: {payload}\n\n"
            return
        async for event in request.app.state.distillation_event_broker.subscribe(str(distillation_id), after_sequence):
            envelope = event.model_copy(update={"request_id": distillation_id})
            data = json.dumps(envelope.model_dump(mode="json"), ensure_ascii=False)
            yield f"id: {event.sequence}\ndata: {data}\n\n"
            if event.type in {"done", "error"}:
                return
    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/memories", response_model=MemoryItemPage)
def list_memories(request: Request, repository_ids_raw: str = Query(alias="repositoryIds"), kind: str | None = None, cursor: str | None = None):
    try:
        repository_ids = [str(UUID(item)) for item in repository_ids_raw.split(",") if item]
    except ValueError as error:
        raise DomainError("MEMORY_SCOPE_INVALID", "知识库范围无效", 422) from error
    if not repository_ids or kind not in {None, "session_summary", "distillation"}:
        raise DomainError("MEMORY_SCOPE_INVALID", "知识库范围无效", 422)
    try:
        offset = int(base64.urlsafe_b64decode(cursor.encode()).decode()) if cursor else 0
    except Exception as error:
        raise DomainError("INVALID_CURSOR", "分页游标无效", 400) from error
    items: list[MemoryItemView] = []
    with request.app.state.database.session() as db:
        if kind in {None, "session_summary"}:
            for record in db.scalars(select(SessionSummaryRecord).where(SessionSummaryRecord.state == "ready")):
                scopes = json.loads(record.repository_ids_json or "[]")
                if set(scopes) & set(repository_ids):
                    items.append(MemoryItemView(id=record.id, kind="session_summary", title="会话摘要", excerpt=(record.content or "")[:300], repository_ids=scopes, session_id=record.session_id, source_id=record.id))
        if kind in {None, "distillation"}:
            for record in db.scalars(select(DistillationRecord).where(DistillationRecord.state.in_(("saved", "saved_unindexed")))):
                scopes = json.loads(record.repository_ids_json or "[]")
                if set(scopes) & set(repository_ids):
                    items.append(MemoryItemView(id=record.id, kind="distillation", title=record.title, excerpt=record.content[:300], repository_ids=scopes, session_id=record.session_id, source_id=record.id))
    items.sort(key=lambda item: str(item.id))
    page = items[offset:offset + 50]
    next_cursor = base64.urlsafe_b64encode(str(offset + 50).encode()).decode() if offset + 50 < len(items) else None
    return MemoryItemPage(items=page, next_cursor=next_cursor)
