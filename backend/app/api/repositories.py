from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Request, status

from app.schemas.repositories import RepositoryCreate, RepositoryView
from app.schemas.yuque import CreateRepositoryRequest, YuqueRepository
from app.storage.models import RepositoryRecord
from app.storage.repositories import RepositoryStore
from app.yuque.gateway import YuqueGateway

router = APIRouter(prefix="/api/repositories", tags=["repositories"])


def _gateway(request: Request) -> YuqueGateway:
    return cast(YuqueGateway, request.app.state.yuque_gateway)


def _store(request: Request) -> RepositoryStore:
    return cast(RepositoryStore, request.app.state.repository_store)


def _refresh_counts(request: Request, records: list[RepositoryRecord]) -> None:
    document_store = request.app.state.document_store
    store = _store(request)
    for record in records:
        store.set_document_count(record.id, len(document_store.list_for_repository(record.id)))


def _view(record: RepositoryRecord) -> RepositoryView:
    return RepositoryView(
        id=record.id,
        yuque_id=record.yuque_id,
        name=record.name,
        description=record.description,
        yuque_url=record.yuque_url,
        document_count=record.document_count,
        sync_status=record.sync_status,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _upsert(store: RepositoryStore, remote: YuqueRepository) -> RepositoryView:
    return _view(
        store.upsert_remote(
            yuque_id=remote.yuque_id,
            name=remote.name,
            description=None,
            yuque_url=remote.url,
        )
    )


@router.get("", response_model=list[RepositoryView])
async def list_repositories(request: Request) -> list[RepositoryView]:
    store = _store(request)
    for remote in await _gateway(request).list_repositories():
        _upsert(store, remote)
    records = store.list()
    _refresh_counts(request, records)
    return [_view(record) for record in store.list()]


@router.post("", response_model=RepositoryView, status_code=status.HTTP_201_CREATED)
async def create_repository(request: Request, body: RepositoryCreate) -> RepositoryView:
    remote = await _gateway(request).create_repository(CreateRepositoryRequest(name=body.name))
    return _upsert(_store(request), remote)
