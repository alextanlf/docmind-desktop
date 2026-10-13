from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.schemas.common import WireModel


class SyncState(StrEnum):
    synced = "synced"
    local_changed = "local_changed"
    remote_changed = "remote_changed"
    conflict = "conflict"


class ConflictResolution(StrEnum):
    keep_local = "keep_local"
    keep_remote = "keep_remote"
    keep_both = "keep_both"


@dataclass(frozen=True)
class RemoteDocumentState:
    document_id: str
    title: str
    content_sha256: str
    url: str
    #: 目录路径。进快照是必须的：否则远端把文档挪了目录，同步认为"什么都没变"，
    #: 界面上的树就永远停在上一次的位置。默认空元组让既有构造点保持可用。
    path: tuple[str, ...] = ()


class ConflictResolutionInput(WireModel):
    """冲突处置请求体。

    🔴 此前该路由用裸 `await request.json()` 再 `ConflictResolution(payload["resolution"])`，
    非法值/缺字段抛的是 `ValueError`，不经过 DomainError handler -> 直接 500。
    改成 pydantic 入参后由 FastAPI 自动回 422，与本文件其余路由一致。
    """

    resolution: ConflictResolution


class SyncOutcome(WireModel):
    repository_id: str
    added: int
    changed: int
    deleted: int
    unchanged: int
    failed: int
    started_at: str
    finished_at: str
