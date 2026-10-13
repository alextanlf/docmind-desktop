from __future__ import annotations

from datetime import datetime
from typing import Literal

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
    #: 原件的状态。**「没有原件」与「原件丢了」必须可区分** —— 否则前端只能显示
    #: "没有原文"，用户分不清是自己没导入原件，还是文件被挪走了（后者要给出补救入口）。
    #:
    #: * ``none``：这篇文档本来就没有绑定原件（本地新建）。
    #: * ``available``：原件在，且与导入/上次转换时一致。
    #: * ``changed``：原件在，但**无法确认它就是当初转换的那一版**（mtime 或大小变了）。
    #: * ``missing``：绑定过原件，但它现在读不到（被移动、删除，或外接盘不在）。
    original_state: Literal["none", "available", "changed", "missing"] = "none"
    #: 文档里的插图。图的内容（图内文字、识别结果）已经在 content 里参与检索；这里是
    #: 展示所需的部分 —— 客户端据此把图渲染在它所属的那一段正文旁边。
    #: 默认空列表而不是 None：没有图是常态，客户端不必先判空再遍历。
    figures: list[ParsedFigure] = Field(default_factory=list)


class OriginalLocation(WireModel):
    """原件在用户磁盘上的位置 —— **只给主进程看**。

    渲染层从来拿不到它：它只发 `documentId`，主进程向后端要这个位置、验完签名再交给
    系统程序打开。这样"渲染层不能指名任意文件"这条性质不需要为"打开原件"破例。

    没有占位路径：原件不存在时 `path` 与 `pathToken` 都是 ``None``，同时 `state` 会说明
    是"本来就没有原件"（``none``）还是"绑定过但现在读不到"（``missing``）—— 后者界面要
    给的是「重新定位原件」。
    """

    path: str | None = None
    path_token: str | None = None
    state: Literal["none", "available", "changed", "missing"] = "none"


class LinkedOriginalInput(WireModel):
    """重新定位原件：把文档重新绑到用户磁盘上的另一个路径。

    与导入时同一条约束：路径必须带主进程签名（`path_token`），否则渲染层就能把任一篇
    文档指到任意文件上 —— 那等于绕了一圈拿到同样的读取能力。
    """

    path: str = Field(min_length=1, max_length=4096)
    #: 主进程对 `path` 的签名。语义见 `app/document/path_tokens.py`。
    path_token: str | None = None
