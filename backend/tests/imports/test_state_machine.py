from __future__ import annotations

import asyncio

import pytest

from app.api.errors import DomainError
from app.imports.events import InMemoryEventBroker
from app.imports.state_machine import ensure_transition_allowed
from app.storage.models import ImportStatus


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (ImportStatus.PENDING, ImportStatus.PARSING),
        (ImportStatus.PARSING, ImportStatus.UPLOADING),
        (ImportStatus.UPLOADING, ImportStatus.INDEXING),
        (ImportStatus.INDEXING, ImportStatus.COMPLETED),
        (ImportStatus.PENDING, ImportStatus.CANCELLED),
        (ImportStatus.INDEXING, ImportStatus.FAILED),
    ],
)
def test_state_machine_accepts_only_adjacent_or_terminal_transitions(
    source: ImportStatus, target: ImportStatus
) -> None:
    ensure_transition_allowed(source, target)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (ImportStatus.PENDING, ImportStatus.UPLOADING),
        (ImportStatus.PENDING, ImportStatus.FAILED),
        (ImportStatus.PARSING, ImportStatus.INDEXING),
        (ImportStatus.COMPLETED, ImportStatus.CANCELLED),
        (ImportStatus.FAILED, ImportStatus.PARSING),
    ],
)
def test_state_machine_rejects_skips_and_terminal_transitions(
    source: ImportStatus, target: ImportStatus
) -> None:
    with pytest.raises(DomainError) as error:
        ensure_transition_allowed(source, target)

    assert error.value.code == "IMPORT_STATE_CONFLICT"


async def test_events_are_monotonic_replayable_and_terminal_exactly_once() -> None:
    broker = InMemoryEventBroker()
    first = await broker.publish("job-1", "progress", {"progress": 10})
    terminal = await broker.publish("job-1", "done", {"progress": 100})
    duplicate_terminal = await broker.publish("job-1", "error", {"code": "TOO_LATE"})

    events = [event async for event in broker.subscribe("job-1", 0)]

    assert [event.sequence for event in events] == [1, 2]
    assert [event.type for event in events] == ["progress", "done"]
    assert duplicate_terminal == terminal
    assert first.request_id == terminal.request_id


async def test_subscriber_replays_then_receives_live_events() -> None:
    broker = InMemoryEventBroker()
    await broker.publish("job-live", "progress", {"progress": 20})

    async def collect() -> list[int]:
        return [event.sequence async for event in broker.subscribe("job-live", 0)]

    subscriber = asyncio.create_task(collect())
    await asyncio.sleep(0)
    await broker.publish("job-live", "progress", {"progress": 45})
    await broker.publish("job-live", "done", {"progress": 100})

    assert await subscriber == [1, 2, 3]


async def test_broker_retains_only_last_one_hundred_events() -> None:
    broker = InMemoryEventBroker()
    for progress in range(105):
        await broker.publish("job-retained", "progress", {"progress": progress})
    await broker.publish("job-retained", "done", {"progress": 100})

    events = [event async for event in broker.subscribe("job-retained", 0)]

    assert len(events) == 100
    assert events[0].sequence == 7
    assert events[-1].sequence == 106


async def test_reopen_retains_prior_attempt_and_starts_new_request() -> None:
    broker = InMemoryEventBroker()
    first_progress = await broker.publish("job-retry", "progress", {"progress": 25})
    first_terminal = await broker.publish("job-retry", "error", {"code": "FAILED"})

    await broker.reopen("job-retry")
    second_progress = await broker.publish("job-retry", "progress", {"progress": 75})
    second_terminal = await broker.publish("job-retry", "done", {"progress": 100})

    first_attempt = [event async for event in broker.subscribe("job-retry", 0)]
    second_attempt = [event async for event in broker.subscribe("job-retry", 2)]

    assert first_attempt == [first_progress, first_terminal]
    assert second_attempt == [second_progress, second_terminal]
    assert [event.sequence for event in second_attempt] == [3, 4]
    assert first_terminal.request_id != second_progress.request_id
    assert second_progress.request_id == second_terminal.request_id


async def test_broker_publishes_explicit_durable_restart_sequence() -> None:
    broker = InMemoryEventBroker()

    terminal = await broker.publish(
        "job-restarted", "error", {"code": "APP_RESTARTED"}, sequence=7
    )

    assert terminal.sequence == 7
    assert await broker.terminal("job-restarted") == terminal
