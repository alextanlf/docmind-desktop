from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Request, status

from app.api.errors import DomainError
from app.remote.registry import ProviderRegistry
from app.schemas.remote import CreateRemoteRepositoryRequest, RemoteRepository
from app.schemas.repositories import RepositoryCreate, RepositoryUpdate, RepositoryView
from app.storage.models import RepositoryRecord
from app.storage.repositories import RepositoryStore

router = APIRouter(prefix="/api/repositories", tags=["repositories"])


def _registry(request: Request) -> ProviderRegistry:
    return cast(ProviderRegistry, request.app.state.remote_registry)


def _store(request: Request) -> RepositoryStore:
    return cast(RepositoryStore, request.app.state.repository_store)


def _view(record: RepositoryRecord, indexed_document_count: int = 0) -> RepositoryView:
    return RepositoryView(
        id=record.id,
        provider=record.provider,
        remote_id=record.remote_id,
        name=record.name,
        description=record.description,
        remote_url=record.remote_url,
        remote_parent_id=record.remote_parent_id,
        document_count=record.document_count,
        indexed_document_count=indexed_document_count,
        sync_status=record.sync_status,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _refresh_counts(request: Request, records: list[RepositoryRecord]) -> dict[str, int]:
    document_store = request.app.state.document_store
    store = _store(request)
    indexed: dict[str, int] = {}
    for record in records:
        store.set_document_count(record.id, len(document_store.list_for_repository(record.id)))
        indexed[record.id] = document_store.indexed_count_for_repository(record.id)
    return indexed


def _upsert(
    store: RepositoryStore, provider: str, remote: RemoteRepository
) -> RepositoryView:
    return _view(
        store.upsert_remote(
            provider=provider,
            remote_id=remote.remote_id,
            name=remote.name,
            description=None,
            remote_url=remote.url,
        )
    )


@router.get("", response_model=list[RepositoryView])
async def list_repositories(request: Request) -> list[RepositoryView]:
    store = _store(request)
    registry = _registry(request)
    for provider_name in registry.names():
        if not registry.is_configured(provider_name):
            continue
        provider = registry.get(provider_name)
        for remote in await provider.list_repositories():
            _upsert(store, provider_name, remote)
    records = store.list()
    indexed = _refresh_counts(request, records)
    return [_view(record, indexed.get(record.id, 0)) for record in store.list()]


@router.post("", response_model=RepositoryView, status_code=status.HTTP_201_CREATED)
async def create_repository(request: Request, body: RepositoryCreate) -> RepositoryView:
    if body.provider:
        provider = _registry(request).get(body.provider)
        remote = await provider.create_repository(
            CreateRemoteRepositoryRequest(name=body.name)
        )
        return _upsert(_store(request), body.provider, remote)
    return _view(_store(request).create_local(name=body.name))


@router.patch("/{repository_id}", response_model=RepositoryView)
async def update_repository(
    request: Request, repository_id: str, body: RepositoryUpdate
) -> RepositoryView:
    store = _store(request)
    record = store.get(repository_id)
    if record is None:
        raise DomainError("NOT_FOUND", "资源不存在", 404)
    if "remote_parent_id" in body.model_fields_set:
        if not record.provider:
            raise DomainError("REMOTE_NOT_BOUND", "知识库未绑定远程来源", 409, False)
        provider = _registry(request).get(record.provider)
        if not provider.identity.capabilities.parent_node_write:
            raise DomainError(
                "REMOTE_CAPABILITY_UNSUPPORTED",
                "该远程来源不支持指定父目录",
                400,
                False,
            )
        parent_id = body.remote_parent_id.strip() if body.remote_parent_id else None
        record = store.set_remote_parent(repository_id, parent_id or None)
    return _view(record)
