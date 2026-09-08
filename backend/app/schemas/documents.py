from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from app.schemas.common import WireModel

DOCUMENT_CONTENT_MAX_CHARACTERS = 2_000_000
DOCUMENT_CONTENT_MAX_BYTES = 4 * 1024 * 1024


class DocumentInput(WireModel):
    title: str = Field(max_length=240)
    content: str = Field(min_length=1, max_length=DOCUMENT_CONTENT_MAX_CHARACTERS)

    @field_validator("title")
    @classmethod
    def title_is_not_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("content")
    @classmethod
    def content_is_valid_markdown(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        if len(value.encode("utf-8")) > DOCUMENT_CONTENT_MAX_BYTES:
            raise ValueError("value exceeds the byte limit")
        return value


class DocumentDelete(WireModel):
    confirm: bool = False


class DocumentSummary(WireModel):
    id: str
    repository_id: str
    yuque_id: str | None
    title: str
    yuque_url: str | None
    chunk_count: int
    status: str
    remote_deleted: bool
    created_at: datetime
    updated_at: datetime


class DocumentDetail(DocumentSummary):
    content: str
