from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.api.errors import DomainError
from app.imports.batch_service import BatchService
from app.imports.events import InMemoryEventBroker
from app.schemas.batches import ConfirmBatchInput, ConfirmBatchItem, StagedDirectoryBatchRequest
from app.storage.database import Database
from app.storage.models import (
    BatchImportRecord,
    BatchItemDecision,
    BatchItemRecord,
    BatchItemState,
    BatchState,
    DocumentRecord,
    ImportJobRecord,
    ImportStatus,
    RepositoryRecord,
)
from app.storage.repositories import BatchImportStore, DocumentStore, ImportJobStore


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

    def reserve_batch_child(
        self, item, *, repository_id=None, batch=None, session=None
    ):  # type: ignore[no-untyped-def]
        del batch
        job_id = str(uuid4())
        self.reservations.append(item.id)
        self.jobs[job_id] = ImportStatus.PENDING
        if session is not None:
            session.add(
                ImportJobRecord(
                    id=job_id,
                    source_kind="staged_file",
                    source_value=json.dumps({"version": 2, "value": item.id}),
                    repository_id=repository_id,
                    message="waiting",
                )
            )
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

    async def retry(self, job_id: str):
        self.jobs[job_id] = ImportStatus.PENDING
        return self.get(job_id)

    def get(self, job_id: str):
        state = self.jobs[job_id]
        return type("Job", (), {"state": state.value, "error_code": None, "error_message": None, "retryable": False})()


class RacingReservationService:
    def __init__(self, job_store: ImportJobStore, gate: asyncio.Event) -> None:
        self.job_store = job_store
        self.gate = gate
        self.arrivals: list[str] = []

    def reserve_batch_child(
        self,
        item,
        *,
        repository_id=None,
        batch=None,
        session=None,
    ):  # type: ignore[no-untyped-def]
        del batch
        if session is not None:
            job_id = str(uuid4())
            session.add(
                ImportJobRecord(
                    id=job_id,
                    source_kind="staged_file",
                    source_value=json.dumps({"version": 2, "value": item.id}),
                    repository_id=repository_id,
                    message="waiting",
                )
            )
            return job_id

        async def legacy_reservation() -> str:
            self.arrivals.append(item.id)
            if len(self.arrivals) == 2:
                self.gate.set()
            await self.gate.wait()
            job_id = str(uuid4())
            self.job_store.create(
                ImportJobRecord(
                    id=job_id,
                    source_kind="staged_file",
                    source_value=json.dumps({"version": 2, "value": item.id}),
                    repository_id=repository_id,
                    message="waiting",
                )
            )
            return job_id

        return legacy_reservation()


class RetryWhileActiveImportService(FakeImportService):
    def __init__(self) -> None:
        super().__init__()
        self.item_jobs: dict[str, str] = {}
        self.attempts: dict[str, int] = {}
        self.failed_once = asyncio.Event()
        self.sibling_active = asyncio.Event()
        self.release_sibling = asyncio.Event()

    def reserve_batch_child(self, item, **kwargs):  # type: ignore[no-untyped-def]
        job_id = super().reserve_batch_child(item, **kwargs)
        self.item_jobs[item.id] = job_id
        return job_id

    async def run(self, job_id: str) -> None:
        item_id = next(item_id for item_id, value in self.item_jobs.items() if value == job_id)
        attempt = self.attempts.get(job_id, 0) + 1
        self.attempts[job_id] = attempt
        if item_id == min(self.item_jobs) and attempt == 1:
            self.jobs[job_id] = ImportStatus.FAILED
            self.failed_once.set()
            return
        if item_id != min(self.item_jobs):
            self.jobs[job_id] = ImportStatus.PARSING
            self.sibling_active.set()
            await self.release_sibling.wait()
        self.jobs[job_id] = ImportStatus.COMPLETED

    async def retry(self, job_id: str):
        self.jobs[job_id] = ImportStatus.PENDING
        return self.get(job_id)

    def get(self, job_id: str):
        state = self.jobs[job_id]
        return type(
            "Job",
            (),
            {
                "state": state.value,
                "error_code": "TRANSIENT" if state is ImportStatus.FAILED else None,
                "error_message": "retry" if state is ImportStatus.FAILED else None,
                "retryable": state is ImportStatus.FAILED,
            },
        )()


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
    assert imports.max_active == 3
    assert store.get(batch.id).state is BatchState.COMPLETED  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_discover_batch_persists_candidates_and_allowed_actions(tmp_path: Path, database) -> None:
    staging_root = tmp_path / "staging"
    collection_id = "00000000-0000-0000-0000-000000000010"
    staged_id = "00000000-0000-0000-0000-000000000011"
    collection = staging_root / "collections" / collection_id
    items_dir = collection / "items"
    items_dir.mkdir(parents=True)
    payload = b"# Guide\n"
    (items_dir / staged_id).write_bytes(payload)
    (collection / "manifest.json").write_text(
        json.dumps(
            {
                "rootId": "root-1",
                "files": [
                    {
                        "relativePath": "guide.md",
                        "stagedId": staged_id,
                        "mediaType": "text/markdown",
                        "sizeBytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    store = BatchImportStore(database)
    repository_id = "00000000-0000-0000-0000-000000000001"
    with database.session() as session:
        session.add(RepositoryRecord(id=repository_id, yuque_id="remote-1", name="Repo"))
    service = BatchService(
        store=store,
        import_service=FakeImportService(),
        staging_root=staging_root,
    )
    batch = service.create_batch(
        StagedDirectoryBatchRequest(
            kind="staged_directory", sourceId=collection_id, repositoryId=repository_id
        )
    )
    await service.discover_batch(batch.id)
    persisted = store.get(batch.id)
    assert persisted is not None
    assert persisted.state is BatchState.AWAITING_CONFIRMATION, (persisted.error_code, persisted.error_message)
    item = store.list_item_records(batch.id)[0]
    assert json.loads(item.allowed_actions_json) == ["create", "skip"]


@pytest.mark.asyncio
async def test_retry_items_without_selection_retries_all_failed_items(database) -> None:
    store = BatchImportStore(database)
    batch = make_batch(store, count=2)
    imports = FakeImportService()
    service = BatchService(store=store, import_service=imports)
    await service.confirm(batch.id, confirm_all(store, batch))
    items = store.list_item_records(batch.id)
    with database.session() as session:
        parent = session.get(BatchImportRecord, batch.id)
        assert parent is not None
        parent.state = BatchState.COMPLETED_WITH_ERRORS
        for item in items:
            persisted = session.get(BatchItemRecord, item.id)
            assert persisted is not None
            persisted.state = BatchItemState.FAILED
            persisted.retryable = True
    await service.retry_items(batch.id)
    assert all(item.state is BatchItemState.COMPLETED for item in store.list_item_records(batch.id))


@pytest.mark.asyncio
async def test_terminal_event_is_replayed_once_for_completed_with_errors_batch(database) -> None:
    store = BatchImportStore(database)
    batch = make_batch(store, count=0)
    store.set_state(batch.id, BatchState.RUNNING)
    store.set_state(batch.id, BatchState.COMPLETED_WITH_ERRORS, message="partial")
    broker = InMemoryEventBroker()
    service = BatchService(store=store, import_service=FakeImportService(), event_broker=broker)
    await service.ensure_terminal_event(batch.id)
    await service.ensure_terminal_event(batch.id)
    events = [event async for event in broker.subscribe(batch.id, 0)]
    assert len(events) == 1
    assert events[0].type == "done"
    assert events[0].payload["state"] == "completed_with_errors"


def test_discovery_persists_duplicate_target_and_actions(database) -> None:
    store = BatchImportStore(database)
    batch = make_batch(store, count=0)
    item = BatchItemRecord(
        source_identity="folder:root:1.md",
        source_revision="a" * 64,
        title="Doc",
        display_path="1.md",
        media_type="text/markdown",
        size_bytes=1,
        cached_source_json=json.dumps({"cache_id": str(uuid4()), "media_type": "text/markdown", "byte_size": 1, "sha256": "a" * 64}),
        allowed_actions_json=json.dumps(["create", "skip"]),
    )
    with database.session() as session:
        session.add(DocumentRecord(id=str(uuid4()), repository_id="repository-1", source_identity=item.source_identity, content_hash="b" * 64, title="existing"))
    store.insert_discovered_items(batch.id, [item])
    persisted = store.list_item_records(batch.id)[0]
    assert persisted.existing_document_id is not None
    assert json.loads(persisted.allowed_actions_json) == ["update", "skip"]


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
    assert error.value.code == "BATCH_STALE_CONFIRMATION"
    assert ImportJobStore(database).list() == []
    assert store.get(batch.id).state is BatchState.AWAITING_CONFIRMATION  # type: ignore[union-attr]
    persisted_item = store.get_item(item.id)
    assert persisted_item is not None
    assert (persisted_item.state, persisted_item.decision) == (BatchItemState.DISCOVERED, None)


@pytest.mark.asyncio
async def test_confirm_recalculates_duplicate_target_and_decision_before_creating_jobs(
    database,
) -> None:
    store = BatchImportStore(database)
    batch = make_batch(store, count=1)
    item = store.list_item_records(batch.id)[0]
    with database.session() as session:
        session.add(
            DocumentRecord(
                id=str(uuid4()),
                repository_id="repository-1",
                title="Now exists",
                content_hash="a" * 64,
                source_identity=item.source_identity,
                source_revision=item.source_revision,
            )
        )

    with pytest.raises(DomainError) as error:
        await BatchService(
            store=store,
            import_service=FakeImportService(),
            document_store=DocumentStore(database),
        ).confirm(batch.id, confirm_all(store, batch))

    assert error.value.code == "BATCH_STALE_CONFIRMATION"
    assert ImportJobStore(database).list() == []
    persisted_item = store.get_item(item.id)
    assert persisted_item is not None
    assert (persisted_item.state, persisted_item.decision, persisted_item.selected) == (
        BatchItemState.DISCOVERED,
        None,
        False,
    )


@pytest.mark.asyncio
async def test_confirm_rejects_update_when_current_source_has_no_target(database) -> None:
    store = BatchImportStore(database)
    batch = make_batch(store, count=1)
    item = store.list_item_records(batch.id)[0]
    with database.session() as session:
        persisted = session.get(BatchItemRecord, item.id)
        assert persisted is not None
        persisted.allowed_actions_json = json.dumps(["update", "skip"])

    with pytest.raises(DomainError) as error:
        await BatchService(
            store=store,
            import_service=FakeImportService(),
            document_store=DocumentStore(database),
        ).confirm(
            batch.id,
            ConfirmBatchInput(
                discovery_version=1,
                items=[ConfirmBatchItem(item_id=item.id, decision="update")],
            ),
        )

    assert error.value.code == "BATCH_STALE_CONFIRMATION"
    assert ImportJobStore(database).list() == []
    assert store.get_item(item.id).decision is None  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_independent_coordinators_confirm_exactly_one_child_without_orphans(
    tmp_path,
) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'confirm-race.sqlite3'}")
    database.upgrade()
    try:
        store = BatchImportStore(database)
        batch = make_batch(store, count=1)
        confirmation = confirm_all(store, batch)
        gate = asyncio.Event()
        arrivals: list[str] = []

        class SharedRaceService(RacingReservationService):
            def __init__(self) -> None:
                super().__init__(ImportJobStore(database), gate)
                self.arrivals = arrivals

        first = BatchService(store=BatchImportStore(database), import_service=SharedRaceService())
        second = BatchService(store=BatchImportStore(database), import_service=SharedRaceService())
        results = await asyncio.gather(
            first.confirm(batch.id, confirmation),
            second.confirm(batch.id, confirmation),
            return_exceptions=True,
        )

        assert sum(not isinstance(result, Exception) for result in results) == 1
        jobs = ImportJobStore(database).list()
        items = store.list_item_records(batch.id)
        assert len(jobs) == 1
        assert len(items) == 1
        assert items[0].import_job_id == jobs[0].id
    finally:
        database.engine.dispose()


@pytest.mark.asyncio
async def test_confirmation_rolls_back_all_jobs_when_a_later_reservation_fails(
    database,
) -> None:
    store = BatchImportStore(database)
    batch = make_batch(store, count=2)

    class FailSecondReservation(FakeImportService):
        def reserve_batch_child(self, item, **kwargs):  # type: ignore[no-untyped-def]
            if self.reservations:
                raise DomainError("BATCH_SOURCE_CHANGED", "changed", 409, False)
            return super().reserve_batch_child(item, **kwargs)

    with pytest.raises(DomainError) as error:
        await BatchService(
            store=store,
            import_service=FailSecondReservation(),
        ).confirm(batch.id, confirm_all(store, batch))

    assert error.value.code == "BATCH_STALE_CONFIRMATION"
    assert ImportJobStore(database).list() == []
    assert store.get(batch.id).state is BatchState.AWAITING_CONFIRMATION  # type: ignore[union-attr]
    assert all(
        (item.state, item.decision, item.import_job_id)
        == (BatchItemState.DISCOVERED, None, None)
        for item in store.list_item_records(batch.id)
    )


@pytest.mark.asyncio
async def test_retry_is_scheduled_while_a_sibling_task_is_active(database) -> None:
    store = BatchImportStore(database)
    batch = make_batch(store, count=2)
    imports = RetryWhileActiveImportService()
    service = BatchService(store=store, import_service=imports)
    await service.confirm(batch.id, confirm_all(store, batch))
    item_ids = sorted(item.id for item in store.list_item_records(batch.id))
    continuation = asyncio.create_task(service.continue_batch(batch.id))
    await imports.failed_once.wait()
    await imports.sibling_active.wait()

    retry = asyncio.create_task(service.retry_item(batch.id, item_ids[0]))
    for _ in range(100):
        if store.get_item(item_ids[0]).state is BatchItemState.QUEUED:  # type: ignore[union-attr]
            break
        await asyncio.sleep(0)
    imports.release_sibling.set()
    await asyncio.gather(continuation, retry)

    assert store.get_item(item_ids[0]).state is BatchItemState.COMPLETED  # type: ignore[union-attr]
    assert store.get(batch.id).state is BatchState.COMPLETED  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_cancel_marks_all_selected_nonterminal_items_and_preserves_terminal_siblings(
    database,
) -> None:
    store = BatchImportStore(database)
    batch = make_batch(store, count=5)
    imports = FakeImportService()
    service = BatchService(store=store, import_service=imports)
    await service.confirm(batch.id, confirm_all(store, batch))
    items = store.list_item_records(batch.id)
    states = [
        BatchItemState.DISCOVERED,
        BatchItemState.QUEUED,
        BatchItemState.RUNNING,
        BatchItemState.COMPLETED,
        BatchItemState.SKIPPED,
    ]
    with database.session() as session:
        for item, state in zip(items, states, strict=True):
            persisted = session.get(BatchItemRecord, item.id)
            assert persisted is not None
            persisted.state = state
            if state is BatchItemState.SKIPPED:
                persisted.selected = False
                persisted.decision = BatchItemDecision.SKIP

    await service.cancel_batch(batch.id)

    final_states = [item.state for item in store.list_item_records(batch.id)]
    assert final_states == [
        BatchItemState.CANCELLED,
        BatchItemState.CANCELLED,
        BatchItemState.CANCELLED,
        BatchItemState.COMPLETED,
        BatchItemState.SKIPPED,
    ]
    assert store.get(batch.id).state is BatchState.CANCELLED  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_retry_rejects_item_when_parent_is_terminal_and_keeps_item_failed(database) -> None:
    store = BatchImportStore(database)
    batch = make_batch(store, count=1)
    imports = FakeImportService()
    service = BatchService(store=store, import_service=imports)
    await service.confirm(batch.id, confirm_all(store, batch))
    item = store.list_item_records(batch.id)[0]
    with database.session() as session:
        persisted = session.get(BatchItemRecord, item.id)
        assert persisted is not None
        persisted.state = BatchItemState.FAILED
        persisted.retryable = True
        parent = session.get(BatchImportRecord, batch.id)
        assert parent is not None
        parent.state = BatchState.CANCELLED

    with pytest.raises(DomainError) as error:
        await service.retry_item(batch.id, item.id)

    assert error.value.code == "BATCH_STATE_CONFLICT"
    assert store.get_item(item.id).state is BatchItemState.FAILED  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_retry_allows_completed_with_errors_parent(database) -> None:
    store = BatchImportStore(database)
    batch = make_batch(store, count=1)
    imports = FakeImportService()
    service = BatchService(store=store, import_service=imports)
    await service.confirm(batch.id, confirm_all(store, batch))
    item = store.list_item_records(batch.id)[0]
    with database.session() as session:
        persisted = session.get(BatchItemRecord, item.id)
        assert persisted is not None
        persisted.state = BatchItemState.FAILED
        persisted.retryable = True
        parent = session.get(BatchImportRecord, batch.id)
        assert parent is not None
        parent.state = BatchState.COMPLETED_WITH_ERRORS
    await service.retry_item(batch.id, item.id)
    assert store.get_item(item.id).state is BatchItemState.COMPLETED  # type: ignore[union-attr]
    assert store.get(batch.id).state is BatchState.COMPLETED  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_cancel_callback_can_reenter_batch_api(database) -> None:
    store = BatchImportStore(database)
    batch = make_batch(store, count=1)

    class Reentrant(FakeImportService):
        def __init__(self):
            super().__init__()
            self.service = None

        async def cancel(self, job_id):
            await self.service.continue_batch(batch.id)
            return await super().cancel(job_id)

    imports = Reentrant()
    service = BatchService(store=store, import_service=imports)
    imports.service = service
    await service.confirm(batch.id, confirm_all(store, batch))
    item = store.list_item_records(batch.id)[0]
    with database.session() as session:
        session.get(BatchItemRecord, item.id).state = BatchItemState.RUNNING  # type: ignore[union-attr]
    await asyncio.wait_for(service.cancel_batch(batch.id), timeout=1)
    assert store.get(batch.id).state is BatchState.CANCELLED  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_cancel_callback_reentry_does_not_repeat_child_cancellation(database) -> None:
    store = BatchImportStore(database)
    batch = make_batch(store, count=1)

    class RecursiveCancel(FakeImportService):
        def __init__(self) -> None:
            super().__init__()
            self.service = None
            self.cancel_calls = 0

        async def cancel(self, job_id):
            self.cancel_calls += 1
            if self.cancel_calls > 1:
                raise AssertionError("recursive child cancellation")
            await self.service.cancel_batch(batch.id)
            return await super().cancel(job_id)

    imports = RecursiveCancel()
    service = BatchService(store=store, import_service=imports)
    imports.service = service
    await service.confirm(batch.id, confirm_all(store, batch))
    item = store.list_item_records(batch.id)[0]
    with database.session() as session:
        session.get(BatchItemRecord, item.id).state = BatchItemState.RUNNING  # type: ignore[union-attr]

    await service.cancel_batch(batch.id)

    assert imports.cancel_calls == 1
    assert store.get(batch.id).state is BatchState.CANCELLED  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_cancel_callback_cross_task_reentry_does_not_deadlock(database) -> None:
    store = BatchImportStore(database)
    batch = make_batch(store, count=1)

    class CrossTaskCancel(FakeImportService):
        def __init__(self) -> None:
            super().__init__()
            self.service = None
            self.cancel_calls = 0

        async def cancel(self, job_id):
            self.cancel_calls += 1
            if self.cancel_calls == 1:
                child = asyncio.create_task(self.service.cancel_batch(batch.id))
                await child
            return await super().cancel(job_id)

    imports = CrossTaskCancel()
    service = BatchService(store=store, import_service=imports)
    imports.service = service
    await service.confirm(batch.id, confirm_all(store, batch))
    item = store.list_item_records(batch.id)[0]
    with database.session() as session:
        session.get(BatchItemRecord, item.id).state = BatchItemState.RUNNING  # type: ignore[union-attr]

    await asyncio.wait_for(service.cancel_batch(batch.id), timeout=1)

    assert imports.cancel_calls == 1
    assert store.get(batch.id).state is BatchState.CANCELLED  # type: ignore[union-attr]


@pytest.mark.parametrize("terminal", [BatchState.COMPLETED, BatchState.COMPLETED_WITH_ERRORS, BatchState.FAILED, BatchState.CANCELLED])
def test_finish_cancel_is_idempotent_for_terminal_parent(database, terminal) -> None:
    store = BatchImportStore(database)
    batch = make_batch(store, state=terminal, count=1)
    original = store.get(batch.id)
    assert original is not None
    original_message, original_completed = original.message, original.completed_at
    result = store.finish_cancel(batch.id)
    assert result.state is terminal
    assert result.message == original_message
    assert result.completed_at == original_completed


@pytest.mark.asyncio
async def test_cancel_refreshes_aggregate_counts_and_progress(database) -> None:
    store = BatchImportStore(database)
    batch = make_batch(store, count=3)
    imports = FakeImportService()
    service = BatchService(store=store, import_service=imports)
    await service.confirm(batch.id, confirm_all(store, batch))
    items = store.list_item_records(batch.id)
    with database.session() as session:
        session.get(BatchItemRecord, items[0].id).state = BatchItemState.COMPLETED  # type: ignore[union-attr]
        session.get(BatchItemRecord, items[1].id).state = BatchItemState.FAILED  # type: ignore[union-attr]
    result = await service.cancel_batch(batch.id)
    assert (result.state, result.selected_count, result.completed_count, result.failed_count, result.skipped_count, result.progress) == (BatchState.CANCELLED, 3, 1, 1, 0, 100)


def test_confirmation_rejects_unrelated_session_reservation_without_orphan(database) -> None:
    store = BatchImportStore(database)
    batch = make_batch(store, count=1)
    item = store.list_item_records(batch.id)[0]
    external = ImportJobStore(database)

    def reserve(item, **kwargs):
        del kwargs
        job_id = str(uuid4())
        external.create(ImportJobRecord(id=job_id, source_kind="staged_file", source_value="{}", repository_id="repository-1"))
        return job_id

    with pytest.raises((RuntimeError, DomainError)):
        store.confirm_and_reserve(
            batch.id,
            confirm_all(store, batch),
            reserve_child=lambda item, **kwargs: reserve(item, **kwargs),
        )
    assert external.list() == []
    persisted = store.get_item(item.id)
    assert persisted is not None
    assert (persisted.state, persisted.decision, persisted.import_job_id) == (BatchItemState.DISCOVERED, None, None)


def test_confirmation_rejects_direct_session_reservation_without_orphan(database) -> None:
    """A callback must not bypass the supplied confirmation session directly."""
    store = BatchImportStore(database)
    batch = make_batch(store, count=1)

    def reserve(item, **kwargs):
        del item, kwargs
        job_id = str(uuid4())
        with database.session() as session:
            session.add(
                ImportJobRecord(
                    id=job_id,
                    source_kind="staged_file",
                    source_value="{}",
                    repository_id="repository-1",
                    message="waiting",
                )
            )
            session.flush()
        return job_id

    with pytest.raises((RuntimeError, DomainError)):
        store.confirm_and_reserve(
            batch.id,
            confirm_all(store, batch),
            reserve_child=reserve,
        )

    assert ImportJobStore(database).list() == []
    persisted = store.get_item(store.list_item_records(batch.id)[0].id)
    assert persisted is not None
    assert (persisted.state, persisted.decision, persisted.import_job_id) == (
        BatchItemState.DISCOVERED,
        None,
        None,
    )


def test_confirmation_rejects_raw_session_from_another_database_without_orphan(database) -> None:
    """A callback cannot commit a child through another Database instance."""
    store = BatchImportStore(database)
    batch = make_batch(store, count=1)
    other_database = Database("sqlite+pysqlite:///:memory:")
    other_database.upgrade()
    try:
        with other_database.session() as session:
            session.add(RepositoryRecord(id="repository-1", yuque_id="remote-1", name="Repo"))

        def reserve(item, **kwargs):
            del item, kwargs
            job_id = str(uuid4())
            with other_database.session() as session:
                session.add(
                    ImportJobRecord(
                        id=job_id,
                        source_kind="staged_file",
                        source_value="{}",
                        repository_id="repository-1",
                        message="waiting",
                    )
                )
                session.flush()
            return job_id

        with pytest.raises((RuntimeError, DomainError)):
            store.confirm_and_reserve(
                batch.id,
                confirm_all(store, batch),
                reserve_child=reserve,
            )

        assert ImportJobStore(other_database).list() == []
    finally:
        other_database.engine.dispose()


def test_confirmation_accepts_same_session_reservation_after_flush(database) -> None:
    """A callback may persist its child through the supplied session before returning."""
    store = BatchImportStore(database)
    batch = make_batch(store, count=1)

    def reserve(item, **kwargs):
        session = kwargs["session"]
        job = ImportJobRecord(
            id=str(uuid4()),
            source_kind="staged_file",
            source_value=json.dumps({"value": item.id}),
            repository_id=kwargs["repository_id"],
            message="waiting",
        )
        session.add(job)
        session.flush()
        return job.id

    confirmed = store.confirm_and_reserve(
        batch.id,
        confirm_all(store, batch),
        reserve_child=reserve,
    )

    assert confirmed.state is BatchState.RUNNING
    persisted_item = store.list_item_records(batch.id)[0]
    assert persisted_item.import_job_id is not None
    assert ImportJobStore(database).get(persisted_item.import_job_id) is not None


def test_confirmation_rejects_unmapped_same_session_jobs_without_orphans(database) -> None:
    """Every job created by a reservation callback must be returned in its mapping."""
    store = BatchImportStore(database)
    batch = make_batch(store, count=1)

    def reserve(item, **kwargs):
        session = kwargs["session"]
        for suffix in ("mapped", "orphan"):
            session.add(
                ImportJobRecord(
                    id=f"{item.id}-{suffix}",
                    source_kind="staged_file",
                    source_value=json.dumps({"value": item.id}),
                    repository_id=kwargs["repository_id"],
                    message="waiting",
                )
            )
        session.flush()
        return f"{item.id}-mapped"

    with pytest.raises(RuntimeError):
        store.confirm_and_reserve(
            batch.id,
            confirm_all(store, batch),
            reserve_child=reserve,
        )

    assert ImportJobStore(database).list() == []


def test_confirmation_rejects_duplicate_child_job_mapping(database) -> None:
    """A child job cannot be bound to more than one selected item."""
    store = BatchImportStore(database)
    batch = make_batch(store, count=2)

    def reserve_children(items, **kwargs):
        session = kwargs["session"]
        job_id = str(uuid4())
        session.add(
            ImportJobRecord(
                id=job_id,
                source_kind="staged_file",
                source_value=json.dumps({"value": "shared"}),
                repository_id=kwargs["repository_id"],
                message="waiting",
            )
        )
        session.flush()
        return {item.id: job_id for item in items}

    with pytest.raises(RuntimeError):
        store.confirm_and_reserve(
            batch.id,
            confirm_all(store, batch),
            reserve_child=lambda item, **kwargs: pytest.fail("bulk callback expected"),
            reserve_children=reserve_children,
        )

    assert ImportJobStore(database).list() == []


def test_confirmation_does_not_delete_existing_job_on_invalid_mapping(database) -> None:
    """A pre-existing job cannot be smuggled through the reservation marker."""
    store = BatchImportStore(database)
    batch = make_batch(store, count=1)
    existing = ImportJobRecord(
        id=str(uuid4()),
        source_kind="staged_file",
        source_value=json.dumps({"value": "existing"}),
        repository_id="repository-1",
        message="existing",
    )
    ImportJobStore(database).create(existing)

    def reserve(item, **kwargs):
        del item
        kwargs["session"].info.setdefault("batch_reserved_job_ids", set()).add(existing.id)
        return existing.id

    with pytest.raises(RuntimeError):
        store.confirm_and_reserve(
            batch.id,
            confirm_all(store, batch),
            reserve_child=reserve,
        )

    assert ImportJobStore(database).get(existing.id) is not None
    persisted_item = store.list_item_records(batch.id)[0]
    assert persisted_item.import_job_id is None
    assert persisted_item.state is BatchItemState.DISCOVERED


def test_confirmation_invokes_source_validation_before_alternate_reservation(database) -> None:
    store = BatchImportStore(database)
    batch = make_batch(store, count=1)
    confirmation = confirm_all(store, batch)

    class AlternateReservation:
        def validate_batch_sources(self, items, **kwargs):  # type: ignore[no-untyped-def]
            raise DomainError("BATCH_SOURCE_CHANGED", "changed", 409, False)

        def reserve_batch_child(self, item, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("reservation must not run")

    with pytest.raises(DomainError) as error:
        store.confirm_and_reserve(
            batch.id,
            confirmation,
            reserve_child=AlternateReservation().reserve_batch_child,
            validate_sources=AlternateReservation().validate_batch_sources,
        )
    assert error.value.code == "BATCH_STALE_CONFIRMATION"
    assert ImportJobStore(database).list() == []


def test_recovery_marks_discovering_failed_and_running_paused(database) -> None:
    store = BatchImportStore(database)
    discovering = make_batch(store, state=BatchState.DISCOVERING, count=0)
    running = make_batch(store, state=BatchState.RUNNING, count=1)
    service = BatchService(store=store, import_service=FakeImportService())
    assert service.recover_on_startup() == 2
    assert store.get(discovering.id).state is BatchState.FAILED  # type: ignore[union-attr]
    assert store.get(discovering.id).error_code == "BATCH_APP_RESTARTED"  # type: ignore[union-attr]
    assert store.get(running.id).state is BatchState.PAUSED  # type: ignore[union-attr]
