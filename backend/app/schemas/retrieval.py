from __future__ import annotations

from app.schemas.common import WireModel


class RetrievalHit(WireModel):
    chunk_id: str
    document_id: str
    document_title: str
    text: str
    section_path: str | None = None
    page_number: int | None = None
    source_url: str | None = None
    vector_score: float | None = None
    keyword_score: float | None = None
    fused_score: float


class RetrievalResult(WireModel):
    hits: list[RetrievalHit]
    max_score: float
