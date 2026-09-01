from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from app.schemas.common import WireModel


class RepositoryCreate(WireModel):
    name: str = Field(max_length=120)

    @field_validator("name")
    @classmethod
    def name_is_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class RepositoryView(WireModel):
    id: str
    yuque_id: str | None
    name: str
    description: str | None
    yuque_url: str | None
    document_count: int
    indexed_document_count: int
    sync_status: str
    created_at: datetime
    updated_at: datetime
