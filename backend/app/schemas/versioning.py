from __future__ import annotations

from dataclasses import dataclass

from app.schemas.common import WireModel


class DocumentVersion(WireModel):
    document_id: str
    version_no: int
    title: str
    content: str
    content_sha256: str
    created_at: str


@dataclass(frozen=True)
class DiffLine:
    kind: str  # "context" | "added" | "removed"
    text: str


class DocumentDiff(WireModel):
    document_id: str
    base_version: int
    target_version: int
    lines: list[dict[str, str]]
