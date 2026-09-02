from __future__ import annotations

import pytest

from app.api.errors import DomainError
from app.imports.batch_state_machine import transition
from app.storage.models import BatchItemState, BatchState


def test_batch_transitions_require_confirmation_and_are_terminally_immutable() -> None:
    assert transition(BatchState.DISCOVERING, BatchState.AWAITING_CONFIRMATION) is BatchState.AWAITING_CONFIRMATION
    assert transition(BatchState.AWAITING_CONFIRMATION, BatchState.RUNNING) is BatchState.RUNNING
    with pytest.raises(DomainError) as error:
        transition(BatchState.DISCOVERING, BatchState.RUNNING)
    assert error.value.code == "BATCH_STATE_CONFLICT"
    with pytest.raises(DomainError):
        transition(BatchState.COMPLETED, BatchState.RUNNING)


def test_item_terminal_states_cannot_be_mutated() -> None:
    from app.imports.batch_state_machine import ensure_item_mutable

    ensure_item_mutable(BatchItemState.QUEUED)
    with pytest.raises(DomainError) as error:
        ensure_item_mutable(BatchItemState.COMPLETED)
    assert error.value.code == "BATCH_STATE_CONFLICT"
