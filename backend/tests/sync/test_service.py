from __future__ import annotations

import pytest

from app.schemas.sync import RemoteDocumentState
from app.sync.service import IncrementalSyncService


class FakeDoc:
    def __init__(self, yuque_id: str | None) -> None:
        self.yuque_id = yuque_id


class FakeDocumentStore:
    def __init__(self, docs: list[FakeDoc]) -> None:
        self.docs = docs

    def list_for_repository(self, repository_id: str) -> list[FakeDoc]:
        return self.docs


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


def _service(
    *,
    store: FakeSyncStateStore,
    docs: list[FakeDoc],
    remote: list[tuple[RemoteDocumentState, str]],
    refresher: FakeRefresher,
) -> IncrementalSyncService:
    async def reader(repository_id: str) -> list[tuple[RemoteDocumentState, str]]:
        return remote

    return IncrementalSyncService(
        sync_state_store=store,
        snapshot_reader=reader,
        refresher=refresher,
        document_store=FakeDocumentStore(docs),
    )


@pytest.mark.asyncio
async def test_sync_reconciles_bound_documents_only() -> None:
    store = FakeSyncStateStore(
        {
            "doc-A": RemoteDocumentState("doc-A", "A", "hash-A", ""),
            "doc-B": RemoteDocumentState("doc-B", "B1", "hash-B1", ""),
            "doc-D": RemoteDocumentState("doc-D", "D", "hash-D", ""),
        }
    )
    docs = [FakeDoc("doc-A"), FakeDoc("doc-B"), FakeDoc("doc-C"), FakeDoc("doc-D")]
    remote = [
        (RemoteDocumentState("doc-A", "A", "hash-A", ""), "# A"),
        (RemoteDocumentState("doc-B", "B2", "hash-B2", ""), "# B2"),
        (RemoteDocumentState("doc-C", "C", "hash-C", ""), "# C"),
        (RemoteDocumentState("doc-E", "E", "hash-E", ""), "# E"),
    ]
    refresher = FakeRefresher()

    outcome = await _service(
        store=store, docs=docs, remote=remote, refresher=refresher
    ).sync_repository("repo-1")

    assert (outcome.added, outcome.changed, outcome.deleted, outcome.unchanged, outcome.failed) == (
        2, 1, 1, 1, 0,
    )
    assert refresher.upserts == [
        ("repo-1", "doc-B", "B2"),
        ("repo-1", "doc-C", "C"),
        ("repo-1", "doc-E", "E"),
    ]
    assert refresher.deleted == [("repo-1", "doc-D")]


@pytest.mark.asyncio
async def test_sync_continues_after_document_failure() -> None:
    store = FakeSyncStateStore(
        {"doc-A": RemoteDocumentState("doc-A", "A", "hash-A", "")}
    )
    docs = [FakeDoc("doc-A")]
    remote = [(RemoteDocumentState("doc-A", "A2", "hash-A2", ""), "# A2")]
    refresher = FakeRefresher()
    refresher.fail_on.add("doc-A")

    outcome = await _service(
        store=store, docs=docs, remote=remote, refresher=refresher
    ).sync_repository("repo-1")

    assert (outcome.added, outcome.changed, outcome.deleted, outcome.unchanged, outcome.failed) == (
        0, 0, 0, 0, 1,
    )
