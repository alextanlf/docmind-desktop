from __future__ import annotations

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
    warnings: list[str] = Field(default_factory=list)


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
