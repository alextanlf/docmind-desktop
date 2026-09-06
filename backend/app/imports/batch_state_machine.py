from __future__ import annotations

from app.api.errors import DomainError
from app.storage.models import BatchItemState, BatchState

_ALLOWED_TRANSITIONS: dict[BatchState, frozenset[BatchState]] = {
    BatchState.DISCOVERING: frozenset({BatchState.AWAITING_CONFIRMATION, BatchState.FAILED, BatchState.CANCELLED}),
    BatchState.AWAITING_CONFIRMATION: frozenset({BatchState.RUNNING, BatchState.COMPLETED, BatchState.FAILED, BatchState.CANCELLED}),
    BatchState.RUNNING: frozenset(
        {
            BatchState.PAUSED,
            BatchState.COMPLETED,
            BatchState.COMPLETED_WITH_ERRORS,
            BatchState.FAILED,
            BatchState.CANCELLED,
        }
    ),
    BatchState.PAUSED: frozenset(
        {BatchState.RUNNING, BatchState.COMPLETED, BatchState.COMPLETED_WITH_ERRORS, BatchState.CANCELLED}
    ),
    BatchState.COMPLETED: frozenset(),
    BatchState.COMPLETED_WITH_ERRORS: frozenset({BatchState.RUNNING}),
    BatchState.FAILED: frozenset(),
    BatchState.CANCELLED: frozenset(),
}

_TERMINAL_ITEMS = frozenset(
    {BatchItemState.COMPLETED, BatchItemState.SKIPPED, BatchItemState.CANCELLED}
)


def transition(source: BatchState | str, target: BatchState | str) -> BatchState:
    source_state = BatchState(source)
    target_state = BatchState(target)
    if target_state not in _ALLOWED_TRANSITIONS[source_state]:
        raise DomainError("BATCH_STATE_CONFLICT", "批次状态冲突", 409)
    return target_state


def ensure_item_mutable(state: BatchItemState | str) -> None:
    if BatchItemState(state) in _TERMINAL_ITEMS:
        raise DomainError("BATCH_STATE_CONFLICT", "批次项已完成，状态不可修改", 409)


def is_terminal_item(state: BatchItemState | str) -> bool:
    return BatchItemState(state) in _TERMINAL_ITEMS
