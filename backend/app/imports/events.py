from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel

EventType = Literal["progress", "delta", "citations", "done", "error"]
_TERMINAL_TYPES = frozenset({"done", "error"})


class EventEnvelope(BaseModel):
    request_id: UUID
    type: EventType
    sequence: int
    payload: dict[str, Any]


class ImportEventBroker(Protocol):
    async def publish(
        self,
        job_id: str,
        event_type: EventType,
        payload: dict[str, Any],
        *,
        sequence: int | None = None,
    ) -> EventEnvelope: ...

    def subscribe(self, job_id: str, after_sequence: int) -> AsyncIterator[EventEnvelope]: ...

    async def reopen(self, job_id: str, *, clear_history: bool = False) -> None: ...

    async def terminal(self, job_id: str) -> EventEnvelope | None: ...

    async def discard(self, job_id: str) -> None: ...


@dataclass
class _JobEvents:
    events: deque[EventEnvelope]
    request_id: UUID = field(default_factory=uuid4)
    sequence: int = 0
    terminal: EventEnvelope | None = None
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)


class InMemoryEventBroker:
    def __init__(self, retention: int | None = 100) -> None:
        if retention is not None and retention <= 0:
            raise ValueError("event retention must be positive or None")
        self._retention = retention
        self._jobs: dict[str, _JobEvents] = {}
        self._jobs_lock = asyncio.Lock()

    async def _job(self, job_id: str) -> _JobEvents:
        async with self._jobs_lock:
            return self._jobs.setdefault(
                job_id, _JobEvents(events=deque(maxlen=self._retention))
            )

    async def publish(
        self,
        job_id: str,
        event_type: EventType,
        payload: dict[str, Any],
        *,
        sequence: int | None = None,
    ) -> EventEnvelope:
        job = await self._job(job_id)
        async with job.condition:
            if job.terminal is not None:
                return job.terminal
            if sequence is None:
                sequence = job.sequence + 1
            elif sequence <= job.sequence:
                raise ValueError("event sequence must increase")
            job.sequence = sequence
            event = EventEnvelope(
                request_id=job.request_id,
                type=event_type,
                sequence=sequence,
                payload=payload,
            )
            job.events.append(event)
            if event_type in _TERMINAL_TYPES:
                job.terminal = event
            job.condition.notify_all()
            return event

    async def reopen(self, job_id: str, *, clear_history: bool = False) -> None:
        job = await self._job(job_id)
        async with job.condition:
            job.terminal = None
            if clear_history:
                job.events.clear()
            job.request_id = uuid4()

    async def terminal(self, job_id: str) -> EventEnvelope | None:
        job = await self._job(job_id)
        async with job.condition:
            return job.terminal

    async def discard(self, job_id: str) -> None:
        async with self._jobs_lock:
            self._jobs.pop(job_id, None)

    async def subscribe(
        self, job_id: str, after_sequence: int
    ) -> AsyncIterator[EventEnvelope]:
        job = await self._job(job_id)
        cursor = max(after_sequence, 0)
        while True:
            async with job.condition:
                available = [event for event in job.events if event.sequence > cursor]
                while not available:
                    if job.terminal is not None:
                        return
                    await job.condition.wait()
                    available = [event for event in job.events if event.sequence > cursor]
            for event in available:
                cursor = event.sequence
                yield event
                if event.type in _TERMINAL_TYPES:
                    return
