from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Request, status
from pydantic import Field

from app.api.errors import DomainError
from app.api.import_batches import _view
from app.schemas.batches import BatchImportView, CachedSourceRef
from app.schemas.common import WireModel
from app.storage.models import BatchImportRecord, BatchItemRecord, BatchState

router = APIRouter(prefix="/api/web-search", tags=["web-search"])


class SearchImportInput(WireModel):
    repository_id: UUID
    result_ids: list[UUID] = Field(min_length=1, max_length=10)


@router.get("/runs/{run_id}")
def get_run(run_id: UUID, request: Request, session_id: UUID):
    store = request.app.state.search_service.run_store
    run = store.get(str(run_id))
    if run is None or run.session_id != str(session_id):
        raise DomainError("SEARCH_RUN_NOT_FOUND", "搜索任务不存在", 404)
    return {"id": run.id, "status": run.status, "errorCode": run.error_code, "results": [{"id": row.id, "rank": row.rank, "canonicalUrl": row.canonical_url, "title": row.title, "snippet": row.snippet, "content": row.content} for row in store.results(run.id)]}


@router.post("/runs/{run_id}/import-batch", response_model=BatchImportView, status_code=status.HTTP_201_CREATED)
def create_import_batch(run_id: UUID, body: SearchImportInput, request: Request) -> BatchImportView:
    if request.app.state.repository_store.get(str(body.repository_id)) is None:
        raise DomainError("NOT_FOUND", "知识库不存在", 404)
    store = request.app.state.search_service.run_store
    run = store.get(str(run_id))
    if run is None or run.status != "completed":
        raise DomainError("SEARCH_RUN_NOT_READY", "搜索任务尚未完成", 409)
    selected = [row for row in store.results(run.id) if UUID(row.id) in set(body.result_ids)]
    if len(selected) != len(body.result_ids):
        raise DomainError("SEARCH_RESULT_NOT_FOUND", "搜索结果不存在", 404)
    batch = request.app.state.batch_store.create_batch(BatchImportRecord(source_kind="search_results", source_descriptor_json=json.dumps({"searchRunId": run.id}), repository_id=str(body.repository_id), state=BatchState.DISCOVERING, message="正在准备搜索结果"))
    cache_dir = Path(request.app.state.settings.staging_dir) / "remote" / batch.id
    cache_dir.mkdir(parents=True, exist_ok=True)
    items = []
    for row in selected:
        raw = row.content.encode("utf-8")[:50 * 1024]
        digest = hashlib.sha256(raw).hexdigest(); cache_id = uuid4(); target = cache_dir / str(cache_id); partial = target.with_suffix(".partial")
        with partial.open("wb") as handle:
            handle.write(raw); handle.flush(); os.fsync(handle.fileno())
        partial.replace(target)
        items.append(BatchItemRecord(source_identity=f"web:{run.id}:{row.canonical_url}", source_revision=digest, title=row.title, display_path=row.canonical_url, media_type="text/markdown", size_bytes=len(raw), cached_source_json=CachedSourceRef(cache_id=cache_id, media_type="text/markdown", byte_size=len(raw), sha256=digest).model_dump_json(), allowed_actions_json="[]"))
    request.app.state.batch_store.insert_discovered_items(batch.id, items)
    request.app.state.batch_store.set_state(batch.id, BatchState.AWAITING_CONFIRMATION, message="搜索结果等待确认")
    return _view(request.app.state.batch_store.get(batch.id))
