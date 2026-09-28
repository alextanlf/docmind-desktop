from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.api.errors import DomainError
from app.schemas.sync import ConflictResolution
from app.schemas.remote import CreateRemoteDocumentRequest, UpdateRemoteDocumentRequest


@dataclass(frozen=True)
class ConflictView:
    document_id: str
    title: str
    local_content: str
    remote_content: str


def _read_local_content(document: Any) -> str:
    if not document.markdown_path:
        return ""
    try:
        return Path(document.markdown_path).read_text(encoding="utf-8")
    except OSError:
        return ""


class SyncConflictService:
    def __init__(
        self,
        *,
        document_store: Any,
        sync_state_store: Any,
        snapshot_reader: Any,
        registry: Any,
        refresher: Any,
        repository_store: Any,
    ) -> None:
        self.document_store = document_store
        self.sync_state_store = sync_state_store
        self.snapshot_reader = snapshot_reader
        self.registry = registry
        self.refresher = refresher
        self.repository_store = repository_store

    async def detect(self, repository_id: str) -> list[ConflictView]:
        previous = self.sync_state_store.get_snapshot(repository_id)
        remote = {
            state.document_id: (state, content)
            for state, content in await self.snapshot_reader(repository_id)
        }
        conflicts: list[ConflictView] = []
        for document in self.document_store.list_for_repository(repository_id):
            if not document.remote_id or not document.local_dirty:
                continue
            entry = remote.get(document.remote_id)
            prior = previous.get(document.remote_id)
            if (
                entry is not None
                and prior is not None
                and entry[0].content_sha256 != prior.content_sha256
            ):
                conflicts.append(
                    ConflictView(
                        document_id=document.id,
                        title=document.title,
                        local_content=_read_local_content(document),
                        remote_content=entry[1],
                    )
                )
        return conflicts

    async def resolve(self, document_id: str, resolution: ConflictResolution) -> None:
        document = self.document_store.get(document_id)
        if document is None or not document.remote_id:
            raise DomainError("MUTATION_NOT_FOUND", "文档变更意图不存在", 409)
        repository = self.repository_store.get(document.repository_id)
        if repository is None or not repository.remote_id or not repository.provider:
            raise DomainError("REMOTE_NOT_BOUND", "知识库尚未绑定远程来源", 409, False)
        provider = self.registry.get(repository.provider)

        local_content = _read_local_content(document)
        if resolution == ConflictResolution.keep_local:
            await provider.update_document(
                UpdateRemoteDocumentRequest(
                    document_id=document.remote_id,
                    title=document.title,
                    content=local_content,
                )
            )
        elif resolution == ConflictResolution.keep_remote:
            remote = await provider.read_document(document.remote_id)
            await self.refresher.upsert_from_remote(
                document.repository_id, document.remote_id, remote.title, remote.content
            )
        elif resolution == ConflictResolution.keep_both:
            remote = await provider.read_document(document.remote_id)
            await provider.create_document(
                CreateRemoteDocumentRequest(
                    repository_id=repository.remote_id,
                    title=f"{remote.title}（远端副本）",
                    content=remote.content,
                )
            )
            await provider.update_document(
                UpdateRemoteDocumentRequest(
                    document_id=document.remote_id,
                    title=document.title,
                    content=local_content,
                )
            )
        else:
            raise DomainError("INVALID_REQUEST", "冲突解决方式无效", 422, False)

        self.document_store.set_sync_state(document_id, "synced")
        self.document_store.set_local_dirty(document_id, False)
