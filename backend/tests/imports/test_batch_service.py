from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest

from app.api.errors import DomainError
from app.imports.batch_service import BatchService
from app.schemas.batches import ConfirmBatchInput, ConfirmBatchItem
from app.storage.models import (
    BatchImportRecord,
    BatchItemRecord,
    BatchState,
    DocumentRecord,
    ImportStatus,
    RepositoryRecord,
)
from app.storage.repositories import BatchImportStore, DocumentStore


def make_batch(store: BatchImportStore, *, state: BatchState = BatchState.AWAITING_CONFIRMATION, count: int = 4) -> BatchImportRecord:
    with store.database.session() as session:
        if session.get(RepositoryRecord, "repository-1") is None:
            session.add(RepositoryRecord(id="repository-1", yuque_id="remote-1", name="Repo"))
    batch = store.create_batch(
        BatchImportRecord(
            id=str(uuid4()),
            source_kind="staged_directory",
            source_descriptor_json=json.dumps({"collectionId": str(uuid4())}),
            repository_id="repository-1",
            state=state,
            discovery_version=1,
            message="ready",
        )
    )
    items = [
        BatchItemRecord(
            id=str(uuid4()),
            source_identity=f"folder:root:{index}.md",
            source_revision=f"sha256:{index}",
            title=f"Doc {index}",
            display_path=f"{index}.md",
            media_type="text/markdown",
            size_bytes=1,
            cached_source_json=json.dumps({"cache_id": str(uuid4()), "media_type": "text/markdown", "byte_size": 1, "sha256": "a" * 64}),
            allowed_actions_json=json.dumps(["create", "skip"]),
        )
        for index in range(count)
    ]
    store.insert_discovered_items(batch.id, items)
    return batch


class FakeImportService:
    def __init__(self) -> None:
        self.jobs: dict[str, ImportStatus] = {}
        self.reservations: list[str] = []
        self.active = 0
        self.max_active = 0
        self.cancelled: list[str] = []
        self.release = asyncio.Event()

    async def reserve_batch_child(self, item):  # type: ignore[no-untyped-def]
        job_id = str(uuid4())
        self.reservations.append(item.id)
        self.jobs[job_id] = ImportStatus.PENDING
        return job_id

    async def run(self, job_id: str) -> None:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.jobs[job_id] = ImportStatus.PARSING
        await asyncio.sleep(0)
        self.active -= 1
        self.jobs[job_id] = ImportStatus.COMPLETED

    async def cancel(self, job_id: str):
        self.cancelled.append(job_id)
        self.jobs[job_id] = ImportStatus.CANCELLED

    def get(self, job_id: str):
        state = self.jobs[job_id]
        return type("Job", (), {"state": state.value, "error_code": None, "error_message": None, "retryable": False})()


def confirm_all(store: BatchImportStore, batch: BatchImportRecord) -> ConfirmBatchInput:
    item_ids = [view.id for view in store.list_items(batch.id).items]
    return ConfirmBatchInput(
        discovery_version=1,
        items=[ConfirmBatchItem(item_id=item_id, decision="create") for item_id in item_ids],
    )


@pytest.mark.asyncio
async def test_scheduler_never_runs_more_than_three_children(database) -> None:
    store = BatchImportStore(database)
    batch = make_batch(store, count=7)
    imports = FakeImportService()
    service = BatchService(store=store, import_service=imports)
    await service.confirm(batch.id, confirm_all(store, batch))
    await service.continue_batch(batch.id)
    assert imports.max_active <= 3
    assert store.get(batch.id).state is BatchState.COMPLETED  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_continue_is_idempotent_and_does_not_reserve_children_twice(database) -> None:
    store = BatchImportStore(database)
    batch = make_batch(store, count=2)
    imports = FakeImportService()
    service = BatchService(store=store, import_service=imports)
    await service.confirm(batch.id, confirm_all(store, batch))
    await service.continue_batch(batch.id)
    await service.continue_batch(batch.id)
    assert len(imports.reservations) == 2


@pytest.mark.asyncio
async def test_cancel_batch_cancels_active_children_and_reaches_terminal_state(database) -> None:
    store = BatchImportStore(database)
    batch = make_batch(store, count=2)
    imports = FakeImportService()
    service = BatchService(store=store, import_service=imports)
    await service.confirm(batch.id, confirm_all(store, batch))
    await service.cancel_batch(batch.id)
    assert store.get(batch.id).state is BatchState.CANCELLED  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_confirm_rejects_stale_existing_document_snapshot(database) -> None:
    store = BatchImportStore(database)
    batch = make_batch(store, count=1)
    item = store.list_item_records(batch.id)[0]
    with database.session() as session:
        document = DocumentRecord(
            id=str(uuid4()), repository_id="repository-1", title="Doc", content_hash="b" * 64
        )
        session.add(document)
    with database.session() as session:
        session.get(BatchItemRecord, item.id).existing_document_id = document.id  # type: ignore[union-attr]
    confirmation = confirm_all(store, batch)
    with pytest.raises(DomainError) as error:
        await BatchService(store=store, import_service=FakeImportService(), document_store=DocumentStore(database)).confirm(batch.id, confirmation)
    assert error.value.code == "BATCH_DISCOVERY_CONFLICT"


def test_recovery_marks_discovering_failed_and_running_paused(database) -> None:
    store = BatchImportStore(database)
    discovering = make_batch(store, state=BatchState.DISCOVERING, count=0)
    running = make_batch(store, state=BatchState.RUNNING, count=1)
    service = BatchService(store=store, import_service=FakeImportService())
    assert service.recover_on_startup() == 2
    assert store.get(discovering.id).state is BatchState.FAILED  # type: ignore[union-attr]
    assert store.get(discovering.id).error_code == "BATCH_APP_RESTARTED"  # type: ignore[union-attr]
    assert store.get(running.id).state is BatchState.PAUSED  # type: ignore[union-attr]
