from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from app.schemas.common import WireModel


class DocumentCitation(WireModel):
    kind: Literal["document"] = "document"
    source_id: str
    chunk_id: str
    document_id: str
    title: str
    section_path: str | None = None
    page_number: int | None = None
    excerpt: str
    source_url: str | None = None


class MemoryCitation(WireModel):
    kind: Literal["memory"] = "memory"
    source_id: str
    memory_kind: Literal["session_summary", "distillation"]
    memory_id: str
    title: str
    excerpt: str
    session_id: str | None = None


Citation = Annotated[DocumentCitation | MemoryCitation, Field(discriminator="kind")]
CitationRef = DocumentCitation


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
    citations: list[Citation]
    generation_status: str
    created_at: datetime
