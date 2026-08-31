from __future__ import annotations

from app.api.errors import DomainError
from app.storage.models import ImportStatus

_ALLOWED_TRANSITIONS: dict[ImportStatus, frozenset[ImportStatus]] = {
    ImportStatus.PENDING: frozenset({ImportStatus.PARSING, ImportStatus.CANCELLED}),
    ImportStatus.PARSING: frozenset(
        {ImportStatus.UPLOADING, ImportStatus.FAILED, ImportStatus.CANCELLED}
    ),
    ImportStatus.UPLOADING: frozenset(
        {ImportStatus.INDEXING, ImportStatus.FAILED, ImportStatus.CANCELLED}
    ),
    ImportStatus.INDEXING: frozenset(
        {ImportStatus.COMPLETED, ImportStatus.FAILED, ImportStatus.CANCELLED}
    ),
    ImportStatus.COMPLETED: frozenset(),
    ImportStatus.FAILED: frozenset(),
    ImportStatus.CANCELLED: frozenset(),
}


def ensure_transition_allowed(source: ImportStatus, target: ImportStatus) -> None:
    if target not in _ALLOWED_TRANSITIONS[source]:
        raise DomainError("IMPORT_STATE_CONFLICT", "导入任务状态冲突", 409)
