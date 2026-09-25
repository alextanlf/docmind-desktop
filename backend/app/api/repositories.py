from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Request, status

from app.schemas.repositories import RepositoryCreate, RepositoryView
from app.schemas.yuque import CreateRepositoryRequest, YuqueRepository
from app.storage.models import RepositoryRecord
from app.storage.repositories import RepositoryStore
from app.yuque.api_gateway import RoutingYuqueGateway
from app.yuque.gateway import YuqueGateway

router = APIRouter(prefix="/api/repositories", tags=["repositories"])


def _gateway(request: Request) -> YuqueGateway:
    return cast(YuqueGateway, request.app.state.yuque_gateway)


def _store(request: Request) -> RepositoryStore:
    return cast(RepositoryStore, request.app.state.repository_store)


def _has_remote_binding(request: Request) -> bool:
    gateway = request.app.state.yuque_gateway
    if not isinstance(gateway, RoutingYuqueGateway):
        # Injected test/fake gateways do not represent a real browser session.
        return True
    setting_store = request.app.state.settings_service.setting_store
    if setting_store.get("yuque-api.verified") == "true":
        return True
    web_state = setting_store.get("yuque-web.connected")
    if web_state is not None:
        return web_state == "true"
    # Upgrade compatibility: installations that already synced Yuque data keep
    # refreshing it, while a fresh installation never launches Yuque implicitly.
    return any(record.yuque_id for record in _store(request).list())


def _refresh_counts(request: Request, records: list[RepositoryRecord]) -> dict[str, int]:
    document_store = request.app.state.document_store
    store = _store(request)
    indexed: dict[str, int] = {}
    for record in records:
        store.set_document_count(record.id, len(document_store.list_for_repository(record.id)))
        indexed[record.id] = document_store.indexed_count_for_repository(record.id)
    return indexed


def _view(record: RepositoryRecord, indexed_document_count: int = 0) -> RepositoryView:
    return RepositoryView(
        id=record.id,
        yuque_id=record.yuque_id,
        name=record.name,
        description=record.description,
        yuque_url=record.yuque_url,
        document_count=record.document_count,
        indexed_document_count=indexed_document_count,
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
    if _has_remote_binding(request):
        for remote in await _gateway(request).list_repositories():
            _upsert(store, remote)
    records = store.list()
    indexed = _refresh_counts(request, records)
    return [_view(record, indexed.get(record.id, 0)) for record in store.list()]


@router.post("", response_model=RepositoryView, status_code=status.HTTP_201_CREATED)
async def create_repository(request: Request, body: RepositoryCreate) -> RepositoryView:
    if body.create_remote:
        remote = await _gateway(request).create_repository(
            CreateRepositoryRequest(name=body.name)
        )
        return _upsert(_store(request), remote)
    return _view(_store(request).create_local(name=body.name))
