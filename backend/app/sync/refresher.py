from __future__ import annotations

import asyncio
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid5

from app.api.errors import DomainError
from app.schemas.imports import DownloadedDocument
from app.storage.models import DocumentChunkRecord, DocumentRecord


class DocumentRefresher:
    """Creates or updates a local document from remote markdown and reindexes it."""

    def __init__(
        self,
        *,
        document_store,
        parser,
        chunker,
        embedding_provider,
        vector_store,
    ) -> None:
        self.document_store = document_store
        self.parser = parser
        self.chunker = chunker
        self.embedding_provider = embedding_provider
        self.vector_store = vector_store

    async def upsert_from_remote(
        self, repository_id: str, document_id: str, title: str, content: str
    ) -> str:
        document = self.document_store.find_by_yuque_id(repository_id, document_id)
        if document is None:
            document = self.document_store.create(
                DocumentRecord(
                    repository_id=repository_id,
                    yuque_id=document_id,
                    title=title,
                    source_type="remote",
                    status="uploaded",
                )
            )

        parsed = self.parser.parse(
            DownloadedDocument(
                title=title,
                source_url=document_id,
                media_type="text/markdown",
                raw_bytes=content.encode("utf-8"),
            )
        )
        chunks = self.chunker.chunk(parsed)
        status = await self.embedding_provider.ensure_ready()
        if status.state != "ready":
            raise DomainError("INDEX_FAILED", "嵌入模型不可用", 503, True)
        embeddings = await self.embedding_provider.embed_documents(
            [chunk.text for chunk in chunks]
        )

        records = self._chunk_records(document, chunks)
        new_ids = [record.vector_id or record.id for record in records]
        old_ids = self.document_store.vector_ids(document.id)
        stale_ids = [identifier for identifier in old_ids if identifier not in new_ids]

        await asyncio.to_thread(
            self.vector_store.upsert,
            repository_id,
            new_ids,
            [record.text for record in records],
            embeddings,
            [
                {
                    "doc_id": document.id,
                    "doc_title": title,
                    "section_path": record.section_path,
                    "source_url": record.source_url,
                    "chunk_index": record.chunk_index,
                    "source_type": document.source_type,
                    "page_number": record.page_number,
                }
                for record in records
            ],
        )
        self.document_store.replace_chunks(document.id, records)
        if stale_ids:
            await asyncio.to_thread(self.vector_store.delete, repository_id, stale_ids)
        self.document_store.update_synced_content(
            document.id,
            title=title,
            content_hash=sha256(content.encode("utf-8")).hexdigest(),
            source_url=document.source_url or document_id,
        )
        return document.id

    async def mark_remote_deleted(self, repository_id: str, document_id: str) -> None:
        document = self.document_store.find_by_yuque_id(repository_id, document_id)
        if document is not None:
            self.document_store.mark_remote_deleted(document.id)

    @staticmethod
    def _chunk_records(document: DocumentRecord, chunks: list) -> list[DocumentChunkRecord]:
        records: list[DocumentChunkRecord] = []
        for chunk in chunks:
            digest = sha256(chunk.text.encode("utf-8")).hexdigest()
            identifier = str(
                uuid5(NAMESPACE_URL, f"{document.id}:sync:{chunk.chunk_index}:{digest}")
            )
            records.append(
                DocumentChunkRecord(
                    id=identifier,
                    document_id=document.id,
                    repository_id=document.repository_id,
                    chunk_index=chunk.chunk_index,
                    text=chunk.text,
                    section_path=chunk.section_path,
                    page_number=chunk.page_number,
                    token_count=chunk.token_count,
                    source_url=chunk.source_url,
                    vector_id=identifier,
                )
            )
        return records
