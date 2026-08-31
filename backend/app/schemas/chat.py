from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.schemas.common import WireModel


class CitationRef(WireModel):
    source_id: str
    chunk_id: str
    document_id: str
    title: str
    section_path: str | None = None
    page_number: int | None = None
    excerpt: str
    source_url: str | None = None


class ChatStreamRequest(WireModel):
    request_id: UUID
    session_id: str
    message: str
    repository_ids: list[str]


class ChatMessageBody(WireModel):
    message: str
    repository_ids: list[str]
    request_id: UUID


class MessageView(WireModel):
    id: str
    session_id: str
    role: str
    content: str
    citations: list[CitationRef]
    generation_status: str
    created_at: datetime
