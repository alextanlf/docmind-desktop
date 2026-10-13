from __future__ import annotations

from datetime import datetime
from typing import Literal

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


class RepositoryUpdate(WireModel):
    # Feishu maps this to ``parent_node_token``.  Providers without the
    # ``parent_node_write`` capability reject the update.
    remote_parent_id: str | None = Field(default=None, max_length=255)


class RepositoryRename(WireModel):
    #: 新的知识库名。本地库只改这里；绑定远端的库还要看 provider 有没有
    #: ``repository_rename`` 能力 —— 没有就只改本地（见 ``RenameScope``）。
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def name_is_not_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized


class RenameScope(WireModel):
    """一次改名到底改到了哪里。

    **只说结果，不写文案**（文案住在前端）：界面据此决定要不要提示"远端名字没有
    跟着变"。

    * ``local_only``：只有 DocMind 里的名字变了。本地知识库、或 provider 不支持
      改名时都是这个 —— 后者下一次列表刷新会把远端名字拉回来，所以必须说。
    * ``remote``：远端名字也一起改了。
    """

    scope: Literal["local_only", "remote"]


class RepositoryDelete(WireModel):
    # Same convention as document deletion: a destructive call must say so
    # explicitly, and a missing flag is a 400 rather than a silent default.
    confirm: bool = False


class RepositoryView(WireModel):
    id: str
    provider: str | None
    remote_id: str | None
    name: str
    description: str | None
    remote_url: str | None
    remote_parent_id: str | None = None
    document_count: int
    indexed_document_count: int
    sync_status: str
    created_at: datetime
    updated_at: datetime


class RepositoryRenameResult(RenameScope):
    repository: RepositoryView


class RemovedRepositoryView(WireModel):
    """一个被用户从 DocMind 移除的远端知识库。

    带上 ``name`` 与 ``provider``：用户要凭这两样判断"这个该不该恢复"。只给 id
    的话，这个列表就只是一串看不懂的标识符。
    """

    id: str
    provider: str
    remote_id: str
    name: str
    removed_at: datetime
