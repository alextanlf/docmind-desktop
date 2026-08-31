from __future__ import annotations

import asyncio
import os
import tempfile
from contextlib import suppress
from hashlib import sha256
from pathlib import Path
from typing import cast
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import APIRouter, Request, status

from app.api.errors import DomainError
from app.core.embedding import EmbeddingProvider
from app.document.chunker import SemanticChunker
from app.document.parser import DocumentParser
from app.schemas.documents import DocumentDelete, DocumentDetail, DocumentInput, DocumentSummary
from app.schemas.imports import DownloadedDocument
from app.schemas.yuque import CreateYuqueDocumentRequest, UpdateYuqueDocumentRequest, YuqueDocument
from app.storage.models import DocumentChunkRecord, DocumentRecord
from app.storage.repositories import DocumentStore, RepositoryStore, VectorCleanupStore
from app.storage.vectorstore import PersistentVectorStore
from app.yuque.gateway import YuqueGateway

router = APIRouter(tags=["documents"])


def _gateway(request: Request) -> YuqueGateway:
    return cast(YuqueGateway, request.app.state.yuque_gateway)


def _repository_store(request: Request) -> RepositoryStore:
    return cast(RepositoryStore, request.app.state.repository_store)


def _document_store(request: Request) -> DocumentStore:
    return cast(DocumentStore, request.app.state.document_store)


def _embedding_provider(request: Request) -> EmbeddingProvider:
    return cast(EmbeddingProvider, request.app.state.embedding_provider)


def _vector_store(request: Request) -> PersistentVectorStore:
    return cast(PersistentVectorStore, request.app.state.vector_store)


def _parser(request: Request) -> DocumentParser:
    return cast(DocumentParser, request.app.state.document_parser)


def _chunker(request: Request) -> SemanticChunker:
    return cast(SemanticChunker, request.app.state.document_chunker)


def _cleanup_store(request: Request) -> VectorCleanupStore:
    return cast(VectorCleanupStore, request.app.state.vector_cleanup_store)


def _not_found() -> DomainError:
    return DomainError("NOT_FOUND", "资源不存在", 404)


def _summary(document: DocumentRecord, remote: YuqueDocument | None = None) -> DocumentSummary:
    return DocumentSummary(
        id=document.id,
        repository_id=document.repository_id,
        yuque_id=document.yuque_id,
        title=remote.title if remote is not None else document.title,
        yuque_url=remote.url if remote is not None else document.yuque_url,
        chunk_count=document.chunk_count,
        status=document.status,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


def _detail(document: DocumentRecord) -> DocumentDetail:
    try:
        content = Path(document.markdown_path or "").read_text(encoding="utf-8")
    except OSError:
        content = ""
    return DocumentDetail(**_summary(document).model_dump(), content=content)


async def _index(request: Request, document: DocumentRecord, content: str) -> DocumentRecord:
    parsed = _parser(request).parse(
        DownloadedDocument(
            title=document.title,
            source_url=document.yuque_url or document.source_url or "",
            media_type="text/markdown",
            raw_bytes=content.encode("utf-8"),
        )
    )
    chunks = _chunker(request).chunk(parsed)
    provider = _embedding_provider(request)
    readiness = await provider.ensure_ready()
    if readiness.state != "ready":
        raise DomainError("INDEX_FAILED", "嵌入模型不可用", 503, True)
    embeddings = await provider.embed_documents([chunk.text for chunk in chunks])
    records = [
        DocumentChunkRecord(
            id=str(uuid5(NAMESPACE_URL, f"{document.id}:{chunk.chunk_index}:{sha256(chunk.text.encode()).hexdigest()}")),
            document_id=document.id,
            repository_id=document.repository_id,
            chunk_index=chunk.chunk_index,
            text=chunk.text,
            section_path=chunk.section_path,
            page_number=chunk.page_number,
            token_count=chunk.token_count,
            source_url=chunk.source_url,
        )
        for chunk in chunks
    ]
    for record in records:
        record.vector_id = record.id
    old_chunks = _document_store(request).list_chunks(document.id)
    old_snapshot = [
        DocumentChunkRecord(
            id=chunk.id, document_id=chunk.document_id, repository_id=chunk.repository_id,
            chunk_index=chunk.chunk_index, text=chunk.text, section_path=chunk.section_path,
            page_number=chunk.page_number, token_count=chunk.token_count,
            source_url=chunk.source_url, vector_id=chunk.vector_id,
        )
        for chunk in old_chunks
    ]
    old_ids = [chunk.vector_id or chunk.id for chunk in old_chunks]
    new_ids = [record.id for record in records]
    vector_store = _vector_store(request)
    try:
        await asyncio.to_thread(
            vector_store.upsert,
            document.repository_id,
            new_ids,
            [record.text for record in records],
            embeddings,
            [
                {
                    "doc_id": document.id,
                    "doc_title": document.title,
                    "section_path": record.section_path,
                    "source_url": record.source_url,
                    "chunk_index": record.chunk_index,
                    "source_type": document.source_type,
                    "page_number": record.page_number,
                }
                for record in records
            ],
        )
        stale_ids = [identifier for identifier in old_ids if identifier not in new_ids]
        _document_store(request).replace_chunks(document.id, records)
        if stale_ids:
            await asyncio.to_thread(vector_store.delete, document.repository_id, stale_ids)
    except DomainError:
        raise
    except Exception as error:  # pragma: no cover - defensive boundary for storage drivers
        with suppress(Exception):
            await asyncio.to_thread(vector_store.delete, document.repository_id, new_ids)
        if old_snapshot:
            with suppress(Exception):
                _document_store(request).replace_chunks(document.id, old_snapshot)
        raise DomainError("INDEX_FAILED", "写入文档索引失败", 503, True) from error
    indexed = _document_store(request).get(document.id)
    if indexed is None:  # pragma: no cover - database state cannot disappear in a request
        raise _not_found()
    return indexed


def _persist_content(request: Request, document: DocumentRecord, content: str) -> None:
    directory = request.app.state.settings.documents_dir / document.id
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    markdown_path = directory / "document.md"
    fd, temporary = tempfile.mkstemp(dir=directory, prefix="document.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, markdown_path)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary)
    _document_store(request).update_editor(
        document.id,
        title=document.title,
        yuque_id=document.yuque_id,
        yuque_url=document.yuque_url,
        markdown_path=str(markdown_path),
        source_url=document.yuque_url,
    )


def _remote_failure(error: Exception) -> DomainError:
    if isinstance(error, DomainError) and error.code == "YUQUE_LOGIN_REQUIRED":
        return error
    status_code = error.status_code if isinstance(error, DomainError) else 503
    return DomainError("YUQUE_OPERATION_FAILED", "语雀文档操作失败", status_code, True, "重试语雀操作")


@router.get("/api/repositories/{repository_id}/documents", response_model=list[DocumentSummary])
async def list_documents(request: Request, repository_id: str) -> list[DocumentSummary]:
    repository = _repository_store(request).get(repository_id)
    if repository is None:
        raise _not_found()
    remote_documents: dict[str, YuqueDocument] = {}
    if repository.yuque_id:
        remote_documents = {
            document.yuque_id: document
            for document in await _gateway(request).list_documents(repository.yuque_id)
        }
    return [
        _summary(document, remote_documents.get(document.yuque_id or ""))
        for document in _document_store(request).list_for_repository(repository_id)
    ]


@router.post(
    "/api/repositories/{repository_id}/documents",
    response_model=DocumentDetail,
    status_code=status.HTTP_201_CREATED,
)
async def create_document(request: Request, repository_id: str, body: DocumentInput) -> DocumentDetail:
    repository = _repository_store(request).get(repository_id)
    if repository is None or not repository.yuque_id:
        raise _not_found()
    try:
        remote = await _gateway(request).create_document(
            CreateYuqueDocumentRequest(
                repository_id=repository.yuque_id, title=body.title, content=body.content
            )
        )
    except Exception as error:  # noqa: BLE001 - gateway boundary maps all failures
        raise _remote_failure(error) from None
    document = _document_store(request).create(
        DocumentRecord(
            id=str(uuid4()),
            repository_id=repository.id,
            yuque_id=remote.yuque_id,
            title=remote.title,
            source_url=remote.url,
            source_type="yuque",
            status="uploaded",
            yuque_url=remote.url,
        )
    )
    try:
        indexed = await _index(request, _document_store(request).get(document.id) or document, body.content)
        _persist_content(request, indexed, body.content)
    except Exception:
        with suppress(Exception):
            await _gateway(request).delete_document(remote.yuque_id)
        _document_store(request).delete_local(document.id)
        raise
    persisted = _document_store(request).get(document.id)
    if persisted is None:
        raise _not_found()
    return _detail(persisted)


@router.get("/api/documents/{document_id}", response_model=DocumentDetail)
async def read_document(request: Request, document_id: str) -> DocumentDetail:
    document = _document_store(request).get(document_id)
    if document is None:
        raise _not_found()
    return _detail(document)


@router.put("/api/documents/{document_id}", response_model=DocumentDetail)
async def update_document(request: Request, document_id: str, body: DocumentInput) -> DocumentDetail:
    document = _document_store(request).get(document_id)
    if document is None or not document.yuque_id:
        raise _not_found()
    try:
        remote = await _gateway(request).update_document(
            UpdateYuqueDocumentRequest(document_id=document.yuque_id, title=body.title, content=body.content)
        )
    except Exception as error:  # noqa: BLE001 - gateway boundary maps all failures
        raise _remote_failure(error) from None
    old_content = _detail(document).content
    old_title = document.title
    try:
        document.title = remote.title
        document.yuque_url = remote.url
        indexed = await _index(request, document, body.content)
        indexed.title = remote.title
        document = _document_store(request).update_editor(
            document.id, title=remote.title, yuque_id=remote.yuque_id,
            yuque_url=remote.url, markdown_path=document.markdown_path or "", source_url=remote.url,
        )
        _persist_content(request, document, body.content)
    except Exception:
        with suppress(Exception):
            await _gateway(request).update_document(
                UpdateYuqueDocumentRequest(document_id=document.yuque_id, title=old_title, content=old_content)
            )
        raise
    return _detail(indexed)


@router.delete("/api/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(request: Request, document_id: str, body: DocumentDelete) -> None:
    document = _document_store(request).get(document_id)
    if document is None:
        import json
        pending = next((item for item in _cleanup_store(request).list() if item.document_id == document_id), None)
        if pending is not None:
            try:
                await asyncio.to_thread(_vector_store(request).delete, pending.repository_id, json.loads(pending.vector_ids_json))
                _cleanup_store(request).delete(pending.id)
            except Exception as error:
                raise DomainError("INDEX_FAILED", "清理文档索引失败", 503, True) from error
            return
        raise _not_found()
    if not body.confirm:
        raise DomainError("CONFIRMATION_REQUIRED", "请确认删除文档", 400)
    if not document.yuque_id:
        raise _not_found()
    try:
        await _gateway(request).delete_document(document.yuque_id)
    except Exception as error:  # noqa: BLE001 - gateway boundary maps all failures
        raise _remote_failure(error) from None
    vector_ids = _document_store(request).vector_ids(document.id)
    try:
        await asyncio.to_thread(_vector_store(request).delete, document.repository_id, vector_ids)
    except Exception:  # noqa: BLE001 - local cleanup must continue after remote success
        _cleanup_store(request).create(document.repository_id, document.id, vector_ids)
    _document_store(request).delete_local(document.id)
