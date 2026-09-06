from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from app.schemas.batches import (
    BatchImportView,
    ConfirmBatchInput,
    ConfirmBatchItem,
    CreateBatchRequest,
)


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
        last_event_sequence=0,
    )

    assert {"sourceKind", "repositoryId", "discoveryVersion", "createdAt"}.issubset(
        batch.model_dump()
    )


def test_create_batch_request_accepts_all_frozen_wire_variants() -> None:
    adapter = TypeAdapter(CreateBatchRequest)
    repository_id = "00000000-0000-0000-0000-000000000001"
    source_id = "00000000-0000-0000-0000-000000000002"
    search_run_id = "00000000-0000-0000-0000-000000000003"
    result_id = "00000000-0000-0000-0000-000000000004"
    staged = adapter.validate_python({"kind": "staged_directory", "sourceId": source_id, "repositoryId": repository_id})
    web = adapter.validate_python({"kind": "web", "entryUrl": "https://example.test/", "repositoryId": repository_id, "maxDepth": 5, "maxPages": 200, "useSitemap": True})
    yuque = adapter.validate_python({"kind": "yuque_repository", "repositoryId": repository_id})
    search = adapter.validate_python({"kind": "search_results", "searchRunId": search_run_id, "resultIds": [result_id], "repositoryId": repository_id})
    assert staged.kind == "staged_directory" and str(staged.source_id) == source_id
    assert web.kind == "web" and str(web.entry_url) == "https://example.test/"
    assert yuque.kind == "yuque_repository" and str(yuque.repository_id) == repository_id
    assert search.kind == "search_results" and [str(value) for value in search.result_ids] == [result_id]


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "staged_directory", "sourceId": "not-a-uuid", "repositoryId": "00000000-0000-0000-0000-000000000001"},
        {"kind": "web", "entryUrl": "ftp://example.test", "repositoryId": "00000000-0000-0000-0000-000000000001", "maxDepth": 5, "maxPages": 200, "useSitemap": True},
        {"kind": "yuque_repository", "sourceId": "00000000-0000-0000-0000-000000000002", "repositoryId": "00000000-0000-0000-0000-000000000001"},
        {"kind": "search_results", "searchRunId": "not-a-uuid", "resultIds": [], "repositoryId": "00000000-0000-0000-0000-000000000001"},
    ],
)
def test_create_batch_request_rejects_foreign_or_malformed_fields(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(CreateBatchRequest).validate_python(payload)
