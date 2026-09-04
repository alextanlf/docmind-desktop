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


class WebCitation(WireModel):
    kind: Literal["web"] = "web"
    source_id: str
    search_run_id: str
    result_id: str
    title: str
    excerpt: str
    source_url: str
    retrieved_at: datetime


Citation = Annotated[DocumentCitation | MemoryCitation | WebCitation, Field(discriminator="kind")]
CitationRef = DocumentCitation


class ChatStreamRequest(WireModel):
    request_id: UUID
    session_id: str
    message: str
    repository_ids: list[str]
    web_search_permission: Literal["inherit", "off", "explicit"] = "inherit"
    existing_user_message_id: str | None = None


class ChatMessageBody(WireModel):
    message: str
    repository_ids: list[str]
    request_id: UUID
    web_search_permission: Literal["inherit", "off", "explicit"] = "inherit"


class ChatSearchContinuation(WireModel):
    request_id: UUID
    repository_ids: list[str]


class MessageView(WireModel):
    id: str
    session_id: str
    role: str
    content: str
    citations: list[Citation]
    generation_status: str
    created_at: datetime
