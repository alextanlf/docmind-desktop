from __future__ import annotations

from dataclasses import dataclass

from app.schemas.common import WireModel


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
