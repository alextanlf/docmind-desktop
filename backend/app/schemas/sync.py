from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.schemas.common import WireModel


class SyncState(StrEnum):
    synced = "synced"
    local_changed = "local_changed"
    remote_changed = "remote_changed"
    conflict = "conflict"


class ConflictResolution(StrEnum):
    keep_local = "keep_local"
    keep_remote = "keep_remote"
    keep_both = "keep_both"


@dataclass(frozen=True)
class RemoteDocumentState:
    document_id: str
    title: str
    content_sha256: str
    url: str


class SyncOutcome(WireModel):
    repository_id: str
    added: int
    changed: int
    deleted: int
    unchanged: int
    failed: int
    started_at: str
    finished_at: str
