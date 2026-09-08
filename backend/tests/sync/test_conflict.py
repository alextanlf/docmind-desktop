from __future__ import annotations

import pytest

from app.schemas.sync import ConflictResolution, RemoteDocumentState
from app.sync.conflict import SyncConflictService


class FakeDoc:
    def __init__(
        self,
        *,
        document_id: str,
        repository_id: str,
        yuque_id: str,
        title: str,
        local_dirty: bool,
        markdown_path: str | None,
    ) -> None:
        self.id = document_id
        self.repository_id = repository_id
        self.yuque_id = yuque_id
        self.title = title
        self.local_dirty = local_dirty
        self.markdown_path = markdown_path
        self.sync_state = "synced"


class FakeDocumentStore:
    def __init__(self, docs: list[FakeDoc]) -> None:
        self.docs = {doc.id: doc for doc in docs}

    def list_for_repository(self, repository_id: str) -> list[FakeDoc]:
        return [doc for doc in self.docs.values() if doc.repository_id == repository_id]

    def get(self, document_id: str) -> FakeDoc | None:
        return self.docs.get(document_id)

    def set_sync_state(self, document_id: str, state: str) -> None:
        self.docs[document_id].sync_state = state

    def set_local_dirty(self, document_id: str, dirty: bool) -> None:
        self.docs[document_id].local_dirty = dirty


class FakeSyncStateStore:
    def __init__(self, snapshot: dict[str, RemoteDocumentState]) -> None:
        self.snapshot = snapshot

    def get_snapshot(self, repository_id: str) -> dict[str, RemoteDocumentState]:
        return dict(self.snapshot)


class FakeGateway:
    def __init__(self) -> None:
        self.updated: list[str] = []
        self.created = 0
        self.remote_content = "# Remote"

    async def update_document(self, request) -> None:
        self.updated.append(request.document_id)

    async def create_document(self, request) -> None:
        self.created += 1

    async def read_document(self, document_id: str):
        class Remote:
            title = "Remote"
            content = self.remote_content

        return Remote()


class FakeRefresher:
    def __init__(self) -> None:
        self.upserts: list[str] = []

    async def upsert_from_remote(
        self, repository_id: str, document_id: str, title: str, content: str
    ) -> str:
        self.upserts.append(document_id)
        return "local"


class FakeRepo:
    def __init__(self, yuque_id: str) -> None:
        self.yuque_id = yuque_id


class FakeRepositoryStore:
    def get(self, repository_id: str) -> FakeRepo | None:
        return FakeRepo("yuque-1")


@pytest.mark.asyncio
async def test_detect_conflict_requires_dirty_and_remote_change() -> None:
    doc = FakeDoc(
        document_id="local-1",
        repository_id="repo-1",
        yuque_id="doc-1",
        title="State",
        local_dirty=True,
        markdown_path=None,
    )
    store = FakeDocumentStore([doc])
    store_for_service = FakeSyncStateStore({"doc-1": RemoteDocumentState("doc-1", "State", "old", "")})

    async def reader(repository_id: str):
        return [(RemoteDocumentState("doc-1", "State", "new", ""), "# New")]

    service = SyncConflictService(
        document_store=store,
        sync_state_store=store_for_service,
        snapshot_reader=reader,
        gateway=FakeGateway(),
        refresher=FakeRefresher(),
        repository_store=FakeRepositoryStore(),
    )
    conflicts = await service.detect("repo-1")
    assert [c.document_id for c in conflicts] == ["local-1"]
    assert conflicts[0].remote_content == "# New"


@pytest.mark.asyncio
async def test_resolve_keep_local_pushes_and_clears_dirty() -> None:
    doc = FakeDoc(
        document_id="local-1",
        repository_id="repo-1",
        yuque_id="doc-1",
        title="State",
        local_dirty=True,
        markdown_path=None,
    )
    store = FakeDocumentStore([doc])
    gateway = FakeGateway()
    service = SyncConflictService(
        document_store=store,
        sync_state_store=FakeSyncStateStore({}),
        snapshot_reader=async_reader([]),
        gateway=gateway,
        refresher=FakeRefresher(),
        repository_store=FakeRepositoryStore(),
    )

    await service.resolve("local-1", ConflictResolution.keep_local)

    assert gateway.updated == ["doc-1"]
    assert store.get("local-1").sync_state == "synced"
    assert store.get("local-1").local_dirty is False


@pytest.mark.asyncio
async def test_resolve_keep_remote_reimports() -> None:
    doc = FakeDoc(
        document_id="local-1",
        repository_id="repo-1",
        yuque_id="doc-1",
        title="State",
        local_dirty=True,
        markdown_path=None,
    )
    refresher = FakeRefresher()
    service = SyncConflictService(
        document_store=FakeDocumentStore([doc]),
        sync_state_store=FakeSyncStateStore({}),
        snapshot_reader=async_reader([]),
        gateway=FakeGateway(),
        refresher=refresher,
        repository_store=FakeRepositoryStore(),
    )

    await service.resolve("local-1", ConflictResolution.keep_remote)

    assert refresher.upserts == ["doc-1"]


@pytest.mark.asyncio
async def test_resolve_keep_both_creates_copy_and_pushes() -> None:
    doc = FakeDoc(
        document_id="local-1",
        repository_id="repo-1",
        yuque_id="doc-1",
        title="State",
        local_dirty=True,
        markdown_path=None,
    )
    gateway = FakeGateway()
    service = SyncConflictService(
        document_store=FakeDocumentStore([doc]),
        sync_state_store=FakeSyncStateStore({}),
        snapshot_reader=async_reader([]),
        gateway=gateway,
        refresher=FakeRefresher(),
        repository_store=FakeRepositoryStore(),
    )

    await service.resolve("local-1", ConflictResolution.keep_both)

    assert gateway.created == 1
    assert gateway.updated == ["doc-1"]


def async_reader(remote):
    async def reader(repository_id: str):
        return remote

    return reader
