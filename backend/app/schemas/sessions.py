from __future__ import annotations

from datetime import datetime

from app.schemas.common import WireModel


class SessionCreate(WireModel):
    repository_ids: list[str]


class SessionSummary(WireModel):
    id: str
    title: str
    repository_ids: list[str]
    created_at: datetime
    updated_at: datetime
    ended_at: datetime | None = None
