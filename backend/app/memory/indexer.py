from __future__ import annotations

import json

from app.api.errors import DomainError
from app.storage.models import DistillationRecord, SessionSummaryRecord


class MemoryIndexer:
    def __init__(self, database, embedding_provider, vector_store) -> None:
        self.database = database
        self.embedding_provider = embedding_provider
        self.vector_store = vector_store

    async def index_summary(self, summary_id: str) -> int:
        with self.database.session() as session:
            record = session.get(SessionSummaryRecord, summary_id)
            if record is None or not record.content:
                raise DomainError("SUMMARY_NOT_FOUND", "摘要不存在", 404)
            return await self._index("session_summary", record.id, record.content, json.loads(record.repository_ids_json))

    async def index_distillation(self, record_id: str) -> int:
        with self.database.session() as session:
            record = session.get(DistillationRecord, record_id)
            if record is None:
                raise DomainError("DISTILLATION_NOT_FOUND", "蒸馏不存在", 404)
            return await self._index("distillation", record.id, record.content, json.loads(record.repository_ids_json))

    async def _index(self, kind: str, source_id: str, content: str, repositories: list[str]) -> int:
        if not repositories:
            raise DomainError("MEMORY_SCOPE_INVALID", "知识库范围不能为空", 400)
        vectors = await self.embedding_provider.embed_documents([content])
        collection = "_session_summaries" if kind == "session_summary" else "_distilled_knowledge"
        for repository_id in repositories:
            identifier = f"memory:{kind}:{source_id}:{repository_id}:0"
            self.vector_store.upsert_memory(collection, [identifier], vectors, [content], [{"repository_id": repository_id, "source_id": source_id, "kind": kind}])
        return len(repositories)
