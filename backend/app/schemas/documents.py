from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from app.schemas.common import WireModel
from app.schemas.imports import ParsedFigure
from app.schemas.repositories import RenameScope

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


class DocumentRename(WireModel):
    """改一篇文档的标题，**不动正文**。

    与 ``DocumentInput``（编辑保存）分开是刻意的：那条流水线会重新分块、重新嵌入、
    写新版本、并把整篇正文回写远端。改名只需要改一个字符串，走那条路等于为了改标题
    重做一遍索引与远端写入。
    """

    title: str = Field(min_length=1, max_length=240)

    @field_validator("title")
    @classmethod
    def title_is_not_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized


class DocumentArchiveRequest(WireModel):
    """批量归档。与批量移除同一套输入形状，语义不同：归档不删任何数据。"""

    document_ids: list[str] = Field(default_factory=list, max_length=500)


class DocumentArchiveResult(WireModel):
    archived: int


class ArchivedDocumentView(WireModel):
    """一篇已归档的文档。

    带 ``repository_name`` 与 ``archived_at``：这份列表住在设置页，用户要在**跨
    知识库**的一堆条目里认出想恢复的那一篇，只有 id 和标题是不够的。
    """

    id: str
    repository_id: str
    repository_name: str
    title: str
    remote_id: str | None
    archived_at: datetime


class DocumentRemoveRequest(WireModel):
    """批量把文档移出 DocMind。只删本地，远端原文不动。"""

    document_ids: list[str] = Field(default_factory=list, max_length=500)


class DocumentRemovalResult(WireModel):
    #: 真正被移出的篇数。列表里不存在的 id 会被跳过而不是报错，所以这个数字可能
    #: 小于请求里的数量 —— 调用方要如实告诉用户到底移了几篇。
    removed: int


class RemovedDocumentView(WireModel):
    """一篇被移出 DocMind 的远端文档（旧流程留下的记录）。

    带 ``title``：用户要凭它判断该不该恢复；只给一个远端 id 的话，这个列表就只是
    一串看不懂的标识符。``repository_name`` 在设置页那份**跨知识库**的列表里用；
    按知识库取的那条路由留空（调用方本来就知道是哪个库）。
    """

    id: str
    repository_id: str
    remote_id: str
    title: str
    repository_name: str = ""
    removed_at: datetime


class RemovedDocumentRestoreResult(WireModel):
    removal_id: str
    repository_id: str
    remote_id: str
    title: str


class DocumentSummary(WireModel):
    id: str
    repository_id: str
    remote_id: str | None
    title: str
    remote_url: str | None
    #: 远端目录路径（逐级名字）。空列表 = 根目录，本地文档恒为空列表。
    #: 只对声明了 ``document_paths`` 的 provider 有实际内容 —— 界面据此决定
    #: 显示树还是平铺，而不是自己猜一层层级出来。
    path: list[str] = Field(default_factory=list)
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


class DocumentRenameResult(RenameScope):
    """改名结果 + 这个词条现在的样子。

    带 ``scope`` 而不是只回文档：远端绑定但 provider 改不了名字时，界面必须能说
    "只在 DocMind 里改了"，否则用户以为远端也跟着改了。
    """

    document: DocumentDetail


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
