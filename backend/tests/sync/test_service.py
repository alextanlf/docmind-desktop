from __future__ import annotations

import pytest

from app.schemas.sync import RemoteDocumentState
from app.sync.service import IncrementalSyncService


class FakeRefresher:
    def __init__(self) -> None:
        self.upserts: list[tuple[str, str, str]] = []
        self.deleted: list[tuple[str, str]] = []
        self.fail_on: set[str] = set()

    async def upsert_from_remote(
        self, repository_id: str, document_id: str, title: str, content: str
    ) -> str:
        if document_id in self.fail_on:
            raise RuntimeError("boom")
        self.upserts.append((repository_id, document_id, title))
        return "local-id"

    async def mark_remote_deleted(self, repository_id: str, document_id: str) -> None:
        self.deleted.append((repository_id, document_id))


class FakeSyncStateStore:
    def __init__(self, snapshot: dict[str, RemoteDocumentState] | None = None) -> None:
        self.snapshot = dict(snapshot or {})
        self.upserts: list[tuple[str, str]] = []
        self.last_synced: str | None = None

    def get_snapshot(self, repository_id: str) -> dict[str, RemoteDocumentState]:
        return dict(self.snapshot)

    def upsert(
        self,
        repository_id: str,
        document_id: str,
        title: str,
        content_sha256: str,
        url: str,
    ) -> None:
        self.upserts.append((repository_id, document_id))

    def set_last_synced_at(self, repository_id: str, iso: str) -> None:
        self.last_synced = iso


@pytest.mark.asyncio
async def test_sync_reimports_added_and_changed_and_skips_unchanged() -> None:
    store = FakeSyncStateStore(
        {
            "doc-A": RemoteDocumentState("doc-A", "A", "hash-A", ""),
            "doc-B": RemoteDocumentState("doc-B", "B1", "hash-B1", ""),
            "doc-D": RemoteDocumentState("doc-D", "D", "hash-D", ""),
        }
    )

    async def reader(repository_id: str) -> list[tuple[RemoteDocumentState, str]]:
        return [
            (RemoteDocumentState("doc-A", "A", "hash-A", ""), "# A"),
            (RemoteDocumentState("doc-B", "B2", "hash-B2", ""), "# B2"),
            (RemoteDocumentState("doc-C", "C", "hash-C", ""), "# C"),
        ]

    refresher = FakeRefresher()
    service = IncrementalSyncService(
        sync_state_store=store, snapshot_reader=reader, refresher=refresher
    )

    outcome = await service.sync_repository("repo-1")

    assert (outcome.added, outcome.changed, outcome.deleted, outcome.unchanged, outcome.failed) == (
        1, 1, 1, 1, 0,
    )
    assert refresher.upserts == [("repo-1", "doc-B", "B2"), ("repo-1", "doc-C", "C")]
    assert refresher.deleted == [("repo-1", "doc-D")]
    assert set(store.upserts) == {("repo-1", "doc-A"), ("repo-1", "doc-B"), ("repo-1", "doc-C")}
    assert store.last_synced is not None


@pytest.mark.asyncio
async def test_sync_continues_after_document_failure() -> None:
    store = FakeSyncStateStore({"doc-A": RemoteDocumentState("doc-A", "A", "hash-A", "")})

    async def reader(repository_id: str) -> list[tuple[RemoteDocumentState, str]]:
        return [
            (RemoteDocumentState("doc-A", "A", "hash-A", ""), "# A"),
            (RemoteDocumentState("doc-B", "B", "hash-B", ""), "# B"),
        ]

    refresher = FakeRefresher()
    refresher.fail_on.add("doc-B")
    service = IncrementalSyncService(
        sync_state_store=store, snapshot_reader=reader, refresher=refresher
    )

    outcome = await service.sync_repository("repo-1")

    assert (outcome.added, outcome.changed, outcome.deleted, outcome.unchanged, outcome.failed) == (
        0, 0, 0, 1, 1,
    )
