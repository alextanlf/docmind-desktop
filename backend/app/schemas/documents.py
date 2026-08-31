from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from app.schemas.common import WireModel


class DocumentInput(WireModel):
    title: str = Field(max_length=240)
    content: str = Field(min_length=1)

    @field_validator("title", "content")
    @classmethod
    def value_is_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
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
    created_at: datetime
    updated_at: datetime


class DocumentDetail(DocumentSummary):
    content: str
