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


class RepositoryUpdate(WireModel):
    # Feishu maps this to ``parent_node_token``.  Providers without the
    # ``parent_node_write`` capability reject the update.
    remote_parent_id: str | None = Field(default=None, max_length=255)


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
