from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.common import WireModel


class DistillationEdit(WireModel):
    title:str=Field(min_length=1,max_length=512); content:str=Field(min_length=1,max_length=200000); key_points:list[str]=Field(default_factory=list,max_length=100)
class DistillationTarget(WireModel):
    target:Literal['local','yuque']; repository_id:UUID|None=None
    @model_validator(mode='after')
    def validate_target(self):
        if self.target=='yuque' and self.repository_id is None: raise ValueError('repository_id required')
        if self.target=='local' and self.repository_id is not None: raise ValueError('repository_id is not allowed for local target')
        return self
class SessionMemorySummaryView(WireModel):
    id:UUID; session_id:UUID; state:Literal['pending','generating','ready','stale','failed']; content:str|None; topics:list[str]; repository_ids:list[UUID]; error_code:str|None; retryable:bool
class MemoryListInput(WireModel):
    repository_ids:list[UUID]=Field(min_length=1,max_length=100); kind:Literal['session_summary','distillation']|None=None; cursor:str|None=None
class MemoryItemView(WireModel):
    id:UUID; kind:Literal['session_summary','distillation']; title:str; excerpt:str; repository_ids:list[UUID]; session_id:UUID|None; source_id:UUID
class MemoryItemPage(WireModel):
    items:list[MemoryItemView]; next_cursor:str|None

class DistillationView(WireModel):
    id: UUID
    session_id: UUID | None
    title: str
    content: str
    key_points: list[str]
    sources: list[dict]
    repository_ids: list[UUID]
    state: Literal['generating','draft','saving','saved','saved_unindexed','failed']
    storage_target: Literal['local','yuque'] | None = None
    local_path: str | None = None
    document_id: str | None = None
    source_url: str | None = None
    error_code: str | None = None
    retryable: bool = False
    created_at: datetime
    updated_at: datetime

class SessionDeleteInput(WireModel):
    confirm: bool
