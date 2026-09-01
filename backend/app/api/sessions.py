from __future__ import annotations

import json
from typing import cast

from fastapi import APIRouter, Request, status

from app.api.errors import DomainError
from app.schemas.chat import CitationRef, MessageView
from app.schemas.sessions import SessionCreate, SessionSummary
from app.storage.models import SessionRecord
from app.storage.repositories import ConversationStore, DocumentStore, RepositoryStore

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


def _conversation_store(request: Request) -> ConversationStore:
    return cast(ConversationStore, request.app.state.conversation_store)


def _repository_store(request: Request) -> RepositoryStore:
    return cast(RepositoryStore, request.app.state.repository_store)

def _document_store(request: Request) -> DocumentStore:
    return cast(DocumentStore, request.app.state.document_store)


def _not_found() -> DomainError:
    return DomainError("NOT_FOUND", "资源不存在", 404)


def _scope(record: SessionRecord) -> list[str]:
    try:
        loaded = json.loads(record.repository_scope_json)
    except (TypeError, json.JSONDecodeError):
        return []
    return loaded if isinstance(loaded, list) and all(isinstance(item, str) for item in loaded) else []


def _view(record: SessionRecord) -> SessionSummary:
    return SessionSummary(
        id=record.id,
        title=record.title or "新会话",
        repository_ids=_scope(record),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _session(request: Request, session_id: str) -> SessionRecord:
    session = _conversation_store(request).get_session(session_id)
    if session is None:
        raise _not_found()
    return session


@router.get("", response_model=list[SessionSummary])
async def list_sessions(request: Request) -> list[SessionSummary]:
    return [_view(record) for record in _conversation_store(request).list_sessions()]


@router.post("", response_model=SessionSummary, status_code=status.HTTP_201_CREATED)
async def create_session(request: Request, body: SessionCreate) -> SessionSummary:
    repository_ids = body.repository_ids
    if any(not identifier.strip() for identifier in repository_ids) or len(set(repository_ids)) != len(
        repository_ids
    ):
        raise DomainError("SESSION_INVALID_REQUEST", "会话知识库范围无效", 400)
    known_ids = {repository.id for repository in _repository_store(request).list()}
    if not set(repository_ids).issubset(known_ids):
        raise _not_found()
    if any(_document_store(request).indexed_count_for_repository(identifier) <= 0 for identifier in repository_ids):
        raise DomainError("SESSION_INVALID_REQUEST", "会话知识库尚未建立索引", 400)
    return _view(_conversation_store(request).create_session(repository_ids, title="新会话"))


@router.get("/{session_id}/messages", response_model=list[MessageView])
async def list_messages(request: Request, session_id: str) -> list[MessageView]:
    _session(request, session_id)
    views: list[MessageView] = []
    for message in _conversation_store(request).list_messages(session_id):
        try:
            citations = [CitationRef.model_validate(item) for item in json.loads(message.citations_json)]
        except (TypeError, ValueError):
            citations = []
        views.append(
            MessageView(
                id=message.id,
                session_id=message.session_id,
                role=message.role,
                content=message.content,
                citations=citations,
                generation_status=message.generation_status,
                created_at=message.created_at,
            )
        )
    return views
