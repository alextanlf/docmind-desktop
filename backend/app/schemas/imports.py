from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field

from app.schemas.common import WireModel


class SourceRef(WireModel):
    kind: Literal["url", "staged_file"]
    value: str


class SourcePreview(WireModel):
    title: str
    source_kind: Literal["url", "staged_file"]
    source_url: str | None = None
    media_type: str
    size_bytes: int
    fingerprint: str
    warnings: list[str] = Field(default_factory=list)

    @classmethod
    def from_document(
        cls, source_kind: Literal["url", "staged_file"], document: DownloadedDocument
    ) -> SourcePreview:
        title = document.title
        if document.media_type == "text/markdown":
            for line in document.raw_bytes.decode("utf-8-sig").splitlines():
                if line.startswith("# "):
                    title = line[2:].strip() or title
                    break
        return cls(
            title=title,
            source_kind=source_kind,
            source_url=document.source_url,
            media_type=document.media_type,
            size_bytes=len(document.raw_bytes),
            fingerprint=hashlib.sha256(document.raw_bytes).hexdigest(),
            warnings=[],
        )


class DownloadedDocument(WireModel):
    title: str
    source_url: str
    media_type: str
    raw_bytes: bytes
    local_path: Path | None = None


class ParsedSection(WireModel):
    heading_path: list[str]
    markdown: str
    page_number: int | None = None


class ParsedDocument(WireModel):
    title: str
    markdown: str
    source_url: str
    sections: list[ParsedSection]


class DocumentChunkDraft(WireModel):
    text: str
    section_path: str
    page_number: int | None = None
    chunk_index: int
    token_count: int
    source_url: str


class ImportCreateRequest(WireModel):
    source: SourceRef
    repository_id: str
    fingerprint: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    duplicate_decision: Literal["skip", "update"] | None = None


class ImportJobView(WireModel):
    id: str
    source: SourceRef
    repository_id: str
    state: Literal[
        "pending", "parsing", "uploading", "indexing", "completed", "failed", "cancelled"
    ]
    current_stage: str | None = None
    progress: int
    message: str
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool
    document_id: str | None = None
    cancel_requested: bool
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime
