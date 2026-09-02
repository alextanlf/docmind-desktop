from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.api.errors import DomainError
from app.schemas.batches import ConfirmBatchInput, ConfirmBatchItem
from app.storage.database import Database
from app.storage.models import (
    BatchImportRecord,
    BatchItemRecord,
    BatchItemState,
    BatchState,
    ImportJobRecord,
)
from app.storage.repositories import BatchImportStore, ImportJobStore


def valid_batch() -> BatchImportRecord:
    return BatchImportRecord(
        id="00000000-0000-0000-0000-000000000001",
        source_kind="staged_directory",
        source_descriptor_json=json.dumps({"collection_id": "collection-1"}),
        repository_id=None,
        state=BatchState.AWAITING_CONFIRMATION,
        discovery_version=1,
        message="Awaiting confirmation",
    )


def valid_item(
    *, item_id: str = "00000000-0000-0000-0000-000000000011", source_identity: str = "folder:r:a.md"
) -> BatchItemRecord:
    return BatchItemRecord(
        id=item_id,
        ordinal=0,
        source_identity=source_identity,
        source_revision="sha256:revision-1",
        title="A",
        display_path="a.md",
        media_type="text/markdown",
        size_bytes=1,
        cached_source_json=json.dumps({"cache_id": "cache-1"}),
        allowed_actions_json=json.dumps(["create", "skip"]),
        state=BatchItemState.DISCOVERED,
    )


def test_batch_item_unique_source_identity_is_scoped_to_batch(database: Database) -> None:
    store = BatchImportStore(database)
    batch = store.create_batch(valid_batch())
    store.insert_discovered_items(batch.id, [valid_item()])

    with pytest.raises(IntegrityError):
        store.insert_discovered_items(batch.id, [valid_item(item_id="00000000-0000-0000-0000-000000000012")])


def test_batch_store_persists_json_and_returns_detached_records(database: Database) -> None:
    store = BatchImportStore(database)
    batch = store.create_batch(valid_batch())
    inserted = store.insert_discovered_items(batch.id, [valid_item()])

    batch.source_descriptor_json = "mutated"
    inserted[0].title = "mutated"

    persisted = store.get(batch.id)
    page = store.list_items(batch.id)
    assert persisted is not None
    assert json.loads(persisted.source_descriptor_json) == {"collection_id": "collection-1"}
    assert page.items[0].title == "A"
    assert page.items[0].allowed_actions == ["create", "skip"]


def test_insert_discovered_items_assigns_ordinals_after_existing_items(database: Database) -> None:
    store = BatchImportStore(database)
    batch = store.create_batch(valid_batch())
    store.insert_discovered_items(batch.id, [valid_item()])

    inserted = store.insert_discovered_items(
        batch.id,
        [valid_item(item_id="00000000-0000-0000-0000-000000000012", source_identity="folder:r:b.md")],
    )

    assert inserted[0].ordinal == 1


def test_confirmation_requires_current_version_and_remote_binding(database: Database) -> None:
    store = BatchImportStore(database)
    batch = store.create_batch(valid_batch())
    item = valid_item()
    item.allowed_actions_json = json.dumps(["attach_remote", "skip"])
    store.insert_discovered_items(batch.id, [item])

    with pytest.raises(DomainError, match="发现结果已更新"):
        store.set_confirmation(
            batch.id,
            ConfirmBatchInput(
                discovery_version=2,
                items=[ConfirmBatchItem(item_id="00000000-0000-0000-0000-000000000011", decision="create")],
            ),
        )
    with pytest.raises(DomainError, match="远端绑定"):
        store.set_confirmation(
            batch.id,
            ConfirmBatchInput(
                discovery_version=1,
                items=[ConfirmBatchItem(item_id="00000000-0000-0000-0000-000000000011", decision="attach_remote")],
            ),
        )
    with database.session() as session:
        session.get(BatchItemRecord, "00000000-0000-0000-0000-000000000011").remote_binding_json = "{}"  # type: ignore[union-attr]
    with pytest.raises(DomainError, match="远端绑定"):
        store.set_confirmation(
            batch.id,
            ConfirmBatchInput(
                discovery_version=1,
                items=[ConfirmBatchItem(item_id="00000000-0000-0000-0000-000000000011", decision="attach_remote")],
            ),
        )
    with pytest.raises(DomainError, match="不允许"):
        store.set_confirmation(
            batch.id,
            ConfirmBatchInput(
                discovery_version=1,
                items=[ConfirmBatchItem(item_id="00000000-0000-0000-0000-000000000011", decision="update")],
            ),
        )


def test_confirmation_reserves_one_job_and_allocates_event_sequences(database: Database) -> None:
    store = BatchImportStore(database)
    batch = store.create_batch(valid_batch())
    store.insert_discovered_items(batch.id, [valid_item()])
    store.set_confirmation(
        batch.id,
        ConfirmBatchInput(
            discovery_version=1,
            items=[ConfirmBatchItem(item_id="00000000-0000-0000-0000-000000000011", decision="create")],
        ),
    )

    ImportJobStore(database).create(
        ImportJobRecord(id="00000000-0000-0000-0000-000000000021", source_kind="staged_file", source_value="{}")
    )
    reserved = store.reserve_child_job(
        "00000000-0000-0000-0000-000000000011", "00000000-0000-0000-0000-000000000021"
    )
    assert reserved.state is BatchItemState.QUEUED
    assert reserved.import_job_id == "00000000-0000-0000-0000-000000000021"
    with pytest.raises(DomainError, match="已绑定"):
        store.reserve_child_job(
            "00000000-0000-0000-0000-000000000011", "00000000-0000-0000-0000-000000000022"
        )
    assert [store.allocate_event_sequence(batch.id), store.allocate_event_sequence(batch.id)] == [1, 2]


def test_allocate_event_sequence_is_atomic_across_concurrent_sessions(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'batch-sequences.sqlite3'}")
    database.upgrade()
    try:
        store = BatchImportStore(database)
        batch = store.create_batch(valid_batch())
        with ThreadPoolExecutor(max_workers=8) as executor:
            sequences = list(executor.map(lambda _: store.allocate_event_sequence(batch.id), range(40)))

        assert sorted(sequences) == list(range(1, 41))
        assert store.get(batch.id).last_event_sequence == 40  # type: ignore[union-attr]
    finally:
        database.engine.dispose()


def test_update_counts_and_recovery_pause_running_batches(database: Database) -> None:
    store = BatchImportStore(database)
    batch = valid_batch()
    batch.state = BatchState.RUNNING
    batch.started_at = datetime.now(UTC)
    store.create_batch(batch)
    store.insert_discovered_items(batch.id, [valid_item()])
    store.update_counts(
        batch.id,
        total_count=1,
        selected_count=1,
        completed_count=1,
        failed_count=0,
        skipped_count=0,
        progress=100,
        message="Complete",
    )

    assert store.recover_on_startup() == 1
    recovered = store.get(batch.id)
    assert recovered is not None
    assert (recovered.state, recovered.completed_count, recovered.progress) == (
        BatchState.PAUSED,
        1,
        100,
    )
