from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.batches import BatchImportView, ConfirmBatchInput, ConfirmBatchItem


def test_batch_confirmation_schema_rejects_more_than_1000_items() -> None:
    item = ConfirmBatchItem(item_id=uuid4(), decision="create")

    with pytest.raises(ValidationError):
        ConfirmBatchInput(discovery_version=1, items=[item] * 1001)


def test_batch_confirmation_schema_rejects_zero_discovery_version() -> None:
    with pytest.raises(ValidationError):
        ConfirmBatchInput(discovery_version=0, items=[])


def test_batch_view_serializes_camel_case_wire_contract() -> None:
    batch = BatchImportView(
        id=uuid4(),
        source_kind="staged_directory",
        repository_id=uuid4(),
        state="awaiting_confirmation",
        discovery_version=1,
        total_count=1,
        selected_count=0,
        completed_count=0,
        failed_count=0,
        skipped_count=0,
        progress=0,
        message="Awaiting confirmation",
        error_code=None,
        error_message=None,
        retryable=False,
        cancel_requested=False,
        created_at=datetime.now(UTC),
        started_at=None,
        completed_at=None,
        updated_at=datetime.now(UTC),
    )

    assert {"sourceKind", "repositoryId", "discoveryVersion", "createdAt"}.issubset(
        batch.model_dump()
    )
