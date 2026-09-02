from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.common import WireModel

BatchSourceKind = Literal["staged_directory", "web", "yuque_repository", "search_results"]
BatchStateValue = Literal[
    "discovering",
    "awaiting_confirmation",
    "running",
    "paused",
    "completed",
    "completed_with_errors",
    "failed",
    "cancelled",
]
BatchItemDecision = Literal["create", "update", "attach_remote", "skip"]
BatchItemStateValue = Literal["discovered", "queued", "running", "completed", "skipped", "failed", "cancelled"]


class BatchImportView(WireModel):
    id: UUID
    source_kind: BatchSourceKind
    repository_id: UUID
    state: BatchStateValue
    discovery_version: int = Field(ge=1)
    total_count: int = Field(ge=0)
    selected_count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    progress: int = Field(ge=0, le=100)
    message: str
    error_code: str | None
    error_message: str | None
    retryable: bool
    cancel_requested: bool
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime


class BatchItemView(WireModel):
    id: UUID
    batch_id: UUID
    ordinal: int = Field(ge=0)
    title: str
    display_path: str
    media_type: str
    size_bytes: int = Field(ge=0)
    source_revision: str
    allowed_actions: list[BatchItemDecision]
    selected: bool
    decision: BatchItemDecision | None
    state: BatchItemStateValue
    import_job_id: UUID | None
    error_code: str | None
    error_message: str | None
    retryable: bool


class BatchItemPage(WireModel):
    items: list[BatchItemView]
    next_cursor: str | None


class ConfirmBatchItem(WireModel):
    item_id: UUID
    decision: BatchItemDecision


class ConfirmBatchInput(WireModel):
    discovery_version: int = Field(ge=1)
    items: list[ConfirmBatchItem] = Field(max_length=1000)
