from __future__ import annotations

import json

from app.api.errors import DomainError
from app.memory.collections import (
    DISTILLATION_COLLECTION,
    SUMMARY_COLLECTION,
    canonical_collection,
)
from app.storage.models import DistillationRecord, SessionSummaryRecord
from app.storage.repositories import MemoryStore


class MemoryIndexer:
    def __init__(self, database, embedding_provider, vector_store) -> None:
        self.database = database
        self.embedding_provider = embedding_provider
        self.vector_store = vector_store
        self.store = MemoryStore(database)

    async def index_summary(self, summary_id: str) -> int:
        with self.database.session() as session:
            record = session.get(SessionSummaryRecord, summary_id)
            if record is None or not record.content:
                raise DomainError("SUMMARY_NOT_FOUND", "摘要不存在", 404)
            if record.state != "ready":
                raise DomainError("SUMMARY_NOT_READY", "摘要尚未就绪", 409)
            return await self._index("session_summary", record.id, record.content, json.loads(record.repository_ids_json), session_id=record.session_id)

    async def index_distillation(self, record_id: str) -> int:
        with self.database.session() as session:
            record = session.get(DistillationRecord, record_id)
            if record is None:
                raise DomainError("DISTILLATION_NOT_FOUND", "蒸馏不存在", 404)
            if record.state not in {"saved", "saved_unindexed"}:
                raise DomainError("DISTILLATION_INVALID", "蒸馏尚未保存", 409)
            return await self._index("distillation", record.id, record.content, json.loads(record.repository_ids_json), session_id=record.session_id)

    async def _index(self, kind: str, source_id: str, content: str, repositories: list[str], *, session_id: str | None) -> int:
        if not repositories:
            raise DomainError("MEMORY_SCOPE_INVALID", "知识库范围不能为空", 400)
        collection = SUMMARY_COLLECTION if kind == "session_summary" else DISTILLATION_COLLECTION
        chunks = [
            (repository_id, 0, content, f"memory:{kind}:{source_id}:{repository_id}:0")
            for repository_id in repositories
        ]
        old_ids, _, cleanup = self.store.replace_memory_chunks(kind, source_id, chunks, collection=collection)
        stale_ids = [identifier for identifier in old_ids if identifier not in {c[3] for c in chunks}]
        vectors = await self.embedding_provider.embed_documents([content])
        for repository_id, _, text, identifier in chunks:
            self.vector_store.upsert_memory(collection, [identifier], vectors, [text], [{"repository_id": repository_id, "source_id": source_id, "kind": kind, "vector_id": identifier, "session_id": session_id or ""}])
        self.store.mark_memory_chunks_indexed([chunk[3] for chunk in chunks])
        if cleanup is not None:
            self.vector_store.delete_memory(collection, stale_ids)
            self.store.delete_vector_cleanup(cleanup.id)
        return len(repositories)

    async def replay_pending_indexes(self) -> int:
        summaries, distillations = self.store.pending_memory_sources()
        completed = 0
        for summary_id in summaries:
            try:
                await self.index_summary(summary_id)
                completed += 1
            except Exception:  # noqa: BLE001, S112 - pending ownership is durable
                continue
        for distillation_id in distillations:
            try:
                await self.retry_unindexed(distillation_id)
                completed += 1
            except Exception:  # noqa: BLE001, S112 - pending ownership is durable
                continue
        return completed

    async def retry_unindexed(self, distillation_id: str):
        await self.index_distillation(distillation_id)
        with self.database.session() as session:
            record = session.get(DistillationRecord, distillation_id)
            record.state = "saved"
            record.error_code = None
            record.retryable = False
        with self.database.session() as session:
            return session.get(DistillationRecord, distillation_id)

    async def replay_cleanups(self) -> int:
        count = 0
        for cleanup in self.store.list_vector_cleanups():
            ids = json.loads(cleanup.vector_ids_json or "[]")
            deletable = [identifier for identifier in ids if identifier not in self.store.owned_vector_ids(ids)]
            if deletable:
                self.vector_store.delete_memory(canonical_collection(cleanup.collection), deletable)
            if not self.store.owned_vector_ids(ids):
                self.store.delete_vector_cleanup(cleanup.id)
                count += 1
        return count
