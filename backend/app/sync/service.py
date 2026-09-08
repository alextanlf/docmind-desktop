from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from app.schemas.sync import RemoteDocumentState, SyncOutcome


class DocumentRefresher(Protocol):
    async def upsert_from_remote(
        self, repository_id: str, document_id: str, title: str, content: str
    ) -> str: ...

    async def mark_remote_deleted(self, repository_id: str, document_id: str) -> None: ...


SnapshotReader = Callable[[str], Awaitable[list[tuple[RemoteDocumentState, str]]]]


class IncrementalSyncService:
    """Diffs a remote snapshot against last-known state and applies it."""

    def __init__(
        self,
        *,
        sync_state_store: Any,
        snapshot_reader: SnapshotReader,
        refresher: DocumentRefresher,
    ) -> None:
        self.sync_state_store = sync_state_store
        self.snapshot_reader = snapshot_reader
        self.refresher = refresher
        self._locks: dict[str, asyncio.Lock] = {}

    async def sync_repository(self, repository_id: str) -> SyncOutcome:
        lock = self._locks.setdefault(repository_id, asyncio.Lock())
        async with lock:
            return await self._sync(repository_id)

    async def _sync(self, repository_id: str) -> SyncOutcome:
        started = datetime.now(UTC).isoformat()
        previous = self.sync_state_store.get_snapshot(repository_id)
        remote = await self.snapshot_reader(repository_id)

        added = changed = deleted = unchanged = failed = 0
        remote_ids: set[str] = set()
        for state, content in remote:
            remote_ids.add(state.document_id)
            prior = previous.get(state.document_id)
            if prior is None:
                if await self._upsert(repository_id, state, content):
                    added += 1
                else:
                    failed += 1
            elif prior.title != state.title or prior.content_sha256 != state.content_sha256:
                if await self._upsert(repository_id, state, content):
                    changed += 1
                else:
                    failed += 1
            else:
                unchanged += 1

        for document_id in previous:
            if document_id not in remote_ids:
                try:
                    await self.refresher.mark_remote_deleted(repository_id, document_id)
                    deleted += 1
                except Exception:  # noqa: BLE001 - one deletion must not stop the repo
                    failed += 1

        for state, _content in remote:
            self.sync_state_store.upsert(
                repository_id,
                state.document_id,
                state.title,
                state.content_sha256,
                state.url,
            )
        finished = datetime.now(UTC).isoformat()
        self.sync_state_store.set_last_synced_at(repository_id, finished)

        return SyncOutcome(
            repository_id=repository_id,
            added=added,
            changed=changed,
            deleted=deleted,
            unchanged=unchanged,
            failed=failed,
            started_at=started,
            finished_at=finished,
        )

    async def _upsert(
        self, repository_id: str, state: RemoteDocumentState, content: str
    ) -> bool:
        try:
            await self.refresher.upsert_from_remote(
                repository_id, state.document_id, state.title, content
            )
            return True
        except Exception:  # noqa: BLE001 - a failed document is recorded, not fatal
            return False
