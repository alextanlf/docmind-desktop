from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from app.schemas.common import WireModel


class RepositoryCreate(WireModel):
    name: str = Field(max_length=120)
    # ``None`` creates a local knowledge base (the default). A provider name
    # such as "yuque" creates the repository on that remote provider instead.
    provider: str | None = Field(default=None, max_length=32)

    @field_validator("name")
    @classmethod
    def name_is_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class RepositoryView(WireModel):
    id: str
    provider: str | None
    remote_id: str | None
    name: str
    description: str | None
    remote_url: str | None
    document_count: int
    indexed_document_count: int
    sync_status: str
    created_at: datetime
    updated_at: datetime
