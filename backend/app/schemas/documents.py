from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from app.schemas.common import WireModel
from app.schemas.imports import ParsedFigure

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
    remote_id: str | None
    title: str
    remote_url: str | None
    chunk_count: int
    status: str
    remote_deleted: bool
    created_at: datetime
    updated_at: datetime


class DocumentDetail(DocumentSummary):
    content: str
    # 原件预览能力：仅当磁盘上存在可识别的原件时才有值。
    # 前端据此决定是否显示「原文」视图；为 None 表示只能看解析结果。
    original_media_type: str | None = None
    original_byte_size: int | None = None
    #: 文档里的插图。图的内容（图内文字、识别结果）已经在 content 里参与检索；这里是
    #: 展示所需的部分 —— 客户端据此把图渲染在它所属的那一段正文旁边。
    #: 默认空列表而不是 None：没有图是常态，客户端不必先判空再遍历。
    figures: list[ParsedFigure] = Field(default_factory=list)
