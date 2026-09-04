from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.api.errors import DomainError
from app.core.llm import ChatRequest, LLMProvider
from app.memory.prompts import merge_request, segment_request, summary_segments
from app.storage.models import SessionRecord, SessionSummaryRecord
from app.storage.repositories import ConversationStore, MemoryStore


class SummaryService:
    def __init__(
        self,
        store: ConversationStore,
        memory_store: MemoryStore,
        *,
        llm: LLMProvider,
        clock: Callable[[], datetime] | None = None,
        indexer=None,
    ) -> None:
        self.store, self.memory_store, self.llm = store, memory_store, llm
        self.clock = clock or (lambda: datetime.now(UTC))
        self.indexer = indexer

    def record_message_activity(self, session_id: str) -> None:
        now = self.clock()
        with self.store.database.session() as session:
            parent = session.get(SessionRecord, session_id)
            if parent is None or parent.ended_at is not None:
                return
            parent.updated_at = now
            parent.summary_due_at = now + timedelta(minutes=30)
            summary = session.scalar(
                select(SessionSummaryRecord).where(SessionSummaryRecord.session_id == session_id)
            )
            if summary is not None and summary.state == "ready":
                summary.state = "stale"
                summary.updated_at = now

    async def regenerate(self, session_id: str):
        return await self._generate(self.memory_store.begin_summary(session_id, self.clock()))

    async def run_due(self, now: datetime | None = None) -> int:
        summary = self.memory_store.claim_due_summary(now or self.clock())
        if summary is None:
            return 0
        await self._generate(summary)
        return 1

    async def end_session(self, session_id: str):
        now = self.clock()
        with self.store.database.session() as session:
            parent = session.get(SessionRecord, session_id)
            if parent is None:
                raise DomainError("SESSION_NOT_FOUND", "会话不存在", 404)
            parent.ended_at = parent.ended_at or now
            parent.summary_due_at = now
        await self.regenerate(session_id)
        return self.store.get_session(session_id)

    async def _generate(self, summary):
        try:
            parts = [
                await self._complete(segment_request(part))
                for part in summary_segments(self.store.list_messages(summary.session_id))
            ] or [""]
            content = parts[0] if len(parts) == 1 else await self._complete(merge_request(parts))
            topics = [
                line.lstrip("#- ").strip()
                for line in content.splitlines()
                if line.startswith(("#", "- "))
            ][:20]
            completed = self.memory_store.complete_summary(
                summary.id, content=content, topics=topics, now=self.clock()
            )
            if self.indexer is not None and completed.repository_ids_json != "[]":
                try:
                    await self.indexer.index_summary(completed.id)
                except Exception:  # noqa: BLE001, S110 - summary remains authoritative in SQLite
                    pass
            return completed
        except Exception:  # noqa: BLE001 - provider boundary must persist a retryable state
            return self.memory_store.fail_summary(summary.id, now=self.clock())

    async def _complete(self, messages) -> str:
        chunks = []
        async for delta in self.llm.stream_chat(ChatRequest(messages=messages)):
            chunks.append(delta.content)
        return "".join(chunks)


class SummaryScheduler:
    def __init__(self, service: SummaryService, *, interval_seconds: float = 5.0) -> None:
        self.service, self.interval_seconds, self._stopped = (
            service,
            interval_seconds,
            asyncio.Event(),
        )

    async def run(self) -> None:
        while not self._stopped.is_set():
            while await self.service.run_due():
                pass
            try:
                await asyncio.wait_for(self._stopped.wait(), self.interval_seconds)
            except TimeoutError:
                pass

    def stop(self) -> None:
        self._stopped.set()
