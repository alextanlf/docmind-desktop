from __future__ import annotations

import json
from typing import cast

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from app.api.errors import DomainError
from app.chat.service import ChatService
from app.schemas.chat import ChatMessageBody, ChatSearchContinuation, ChatStreamRequest
from app.storage.models import SessionRecord
from app.storage.repositories import ConversationStore, DocumentStore, RepositoryStore

router = APIRouter(prefix="/api/sessions", tags=["chat"])


def _invalid_request() -> DomainError:
    return DomainError("CHAT_INVALID_REQUEST", "聊天请求无效", 400)


def _conversation_store(request: Request) -> ConversationStore:
    return cast(ConversationStore, request.app.state.conversation_store)


def _repository_store(request: Request) -> RepositoryStore:
    return cast(RepositoryStore, request.app.state.repository_store)

def _document_store(request: Request) -> DocumentStore:
    return cast(DocumentStore, request.app.state.document_store)


def _chat_service(request: Request) -> ChatService:
    return cast(ChatService, request.app.state.chat_service)


def _session(store: ConversationStore, session_id: str) -> SessionRecord:
    session = next((item for item in store.list_sessions() if item.id == session_id), None)
    if session is None:
        raise _invalid_request()
    return session


def _after_sequence(request: Request) -> int:
    value = request.headers.get("Last-Event-ID")
    if value is None:
        return 0
    try:
        sequence = int(value)
    except ValueError:
        raise _invalid_request() from None
    if sequence < 0:
        raise _invalid_request()
    return sequence


@router.post("/{session_id}/messages/stream")
async def stream_message(request: Request, session_id: str) -> StreamingResponse:
    try:
        raw_body = await request.json()
        body = ChatMessageBody.model_validate(raw_body)
    except (ValueError, TypeError, ValidationError):
        raise _invalid_request() from None

    message = body.message.strip()
    repository_ids = body.repository_ids
    session = _session(_conversation_store(request), session_id)
    if session.ended_at is not None:
        raise DomainError("SESSION_ENDED", "会话已结束", 409)
    try:
        scope = json.loads(session.repository_scope_json)
    except (TypeError, json.JSONDecodeError):
        raise _invalid_request() from None
    known_repository_ids = {repository.id for repository in _repository_store(request).list()}
    if (
        not message
        or not repository_ids
        or any(not repository_id.strip() for repository_id in repository_ids)
        or len(set(repository_ids)) != len(repository_ids)
        or not set(repository_ids).issubset(known_repository_ids)
        or any(_document_store(request).indexed_count_for_repository(identifier) <= 0 for identifier in repository_ids)
        or not isinstance(scope, list)
        or not set(repository_ids).issubset(set(scope))
    ):
        raise _invalid_request()

    chat_request = ChatStreamRequest(
        request_id=body.request_id,
        session_id=session_id,
        message=message,
        repository_ids=repository_ids,
        web_search_permission=body.web_search_permission,
    )
    after_sequence = _after_sequence(request)

    async def stream():  # type: ignore[no-untyped-def]
        async for event in _chat_service(request).stream(
            chat_request, after_sequence=after_sequence
        ):
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


@router.post("/{session_id}/messages/{user_message_id}/web-search/stream")
async def continue_with_web_search(request: Request, session_id: str, user_message_id: str) -> StreamingResponse:
    try:
        body = ChatSearchContinuation.model_validate(await request.json())
    except (ValueError, TypeError, ValidationError):
        raise _invalid_request() from None
    session = _session(_conversation_store(request), session_id)
    if session.ended_at is not None:
        raise DomainError("SESSION_ENDED", "会话已结束", 409)
    original = _conversation_store(request).get_message(user_message_id)
    if original is None or original.session_id != session_id or original.role != "user":
        raise DomainError("SEARCH_INVALID_CONTEXT", "原始用户消息不存在", 404)
    try:
        scope = json.loads(session.repository_scope_json)
    except (TypeError, json.JSONDecodeError):
        raise _invalid_request() from None
    known_repository_ids = {repository.id for repository in _repository_store(request).list()}
    if not body.repository_ids or len(set(body.repository_ids)) != len(body.repository_ids) or not set(body.repository_ids).issubset(known_repository_ids) or not set(body.repository_ids).issubset(set(scope)):
        raise _invalid_request()
    chat_request = ChatStreamRequest(request_id=body.request_id, session_id=session_id, message=original.content, repository_ids=body.repository_ids, web_search_permission="explicit", existing_user_message_id=user_message_id)
    after_sequence = _after_sequence(request)
    async def stream():
        async for event in _chat_service(request).stream(chat_request, after_sequence=after_sequence):
            data = json.dumps(event.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
            yield f"id: {event.sequence}\ndata: {data}\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
