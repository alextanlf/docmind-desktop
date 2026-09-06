from __future__ import annotations

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Query, Request

from app.api.errors import DomainError
from app.schemas.web_search import SearchResultView, SearchRunRequest
from app.search.service import SearchService

router = APIRouter(prefix="/api/search", tags=["search"])

def _service(request: Request) -> SearchService:
    return cast(SearchService, request.app.state.search_service)

def _result(row) -> SearchResultView:
    return SearchResultView(id=UUID(row.id), rank=row.rank, canonical_url=row.canonical_url,
        title=row.title, snippet=row.snippet, content=row.content)

@router.post("/runs")
async def create_run(request: Request, body: SearchRunRequest):
    conversation = request.app.state.conversation_store
    session = conversation.get_session(str(body.session_id))
    message = conversation.get_message(str(body.user_message_id))
    if (
        session is None
        or message is None
        or message.session_id != str(body.session_id)
        or message.role != "user"
    ):
        raise DomainError("SEARCH_INVALID_CONTEXT", "会话或用户消息不存在", 400)
    view = await _service(request).run(body, authorization_mode=body.authorization_mode)
    return {"id": view.id, "status": view.status, "results": [_result(r) for r in view.results]}

@router.get("/runs/{run_id}")
async def get_run(request: Request, run_id: UUID, session_id: Annotated[UUID, Query()]):
    run = _service(request).run_store.get(str(run_id))
    if run is None:
        raise DomainError("SEARCH_RUN_NOT_FOUND", "搜索任务不存在", 404)
    if run.session_id != str(session_id):
        raise DomainError("SEARCH_RUN_NOT_FOUND", "搜索任务不存在", 404)
    return {"id": run.id, "status": run.status, "errorCode": run.error_code,
            "results": [_result(r) for r in _service(request).run_store.results(run.id)]}

@router.get("/runs/{run_id}/results", response_model=list[SearchResultView])
async def list_results(request: Request, run_id: UUID, session_id: Annotated[UUID, Query()], limit: Annotated[int, Query(ge=1, le=100)] = 50, offset: Annotated[int, Query(ge=0, le=10000)] = 0):
    run = _service(request).run_store.get(str(run_id))
    if run is None:
        raise DomainError("SEARCH_RUN_NOT_FOUND", "搜索任务不存在", 404)
    if run.session_id != str(session_id):
        raise DomainError("SEARCH_RUN_NOT_FOUND", "搜索任务不存在", 404)
    rows = _service(request).run_store.results(str(run_id))
    return [_result(r) for r in rows[offset:offset + limit]]
