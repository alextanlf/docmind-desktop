from __future__ import annotations

import asyncio
import json
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
from app.storage.repositories import (
    DocumentMutationStore,
    DocumentStore,
    RepositoryStore,
    VectorCleanupStore,
)
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


def _mutation_store(request: Request) -> DocumentMutationStore:
    return cast(DocumentMutationStore, request.app.state.document_mutation_store)


def _chunk_snapshot(chunk: DocumentChunkRecord) -> dict[str, object]:
    return {
        "id": chunk.id,
        "document_id": chunk.document_id,
        "repository_id": chunk.repository_id,
        "chunk_index": chunk.chunk_index,
        "text": chunk.text,
        "section_path": chunk.section_path,
        "page_number": chunk.page_number,
        "token_count": chunk.token_count,
        "source_url": chunk.source_url,
        "vector_id": chunk.vector_id,
    }


def _document_snapshot(request: Request, document: DocumentRecord) -> dict[str, object]:
    content = ""
    if document.markdown_path:
        try:
            content = Path(document.markdown_path).read_text(encoding="utf-8")
        except OSError:
            content = ""
    return {
        "id": document.id,
        "repository_id": document.repository_id,
        "yuque_id": document.yuque_id,
        "title": document.title,
        "source_url": document.source_url,
        "raw_path": document.raw_path,
        "markdown_path": document.markdown_path,
        "source_type": document.source_type,
        "content_hash": document.content_hash,
        "chunk_count": document.chunk_count,
        "status": document.status,
        "yuque_url": document.yuque_url,
        "created_at": document.created_at.isoformat() if document.created_at else None,
        "updated_at": document.updated_at.isoformat() if document.updated_at else None,
        "content": content,
        "chunks": [_chunk_snapshot(chunk) for chunk in _document_store(request).list_chunks(document.id)],
    }


def _record_from_snapshot(snapshot: dict[str, object]) -> DocumentChunkRecord:
    return DocumentChunkRecord(
        id=str(snapshot["id"]),
        document_id=str(snapshot["document_id"]),
        repository_id=str(snapshot["repository_id"]),
        chunk_index=int(snapshot["chunk_index"]),
        text=str(snapshot["text"]),
        section_path=cast(str | None, snapshot.get("section_path")),
        page_number=cast(int | None, snapshot.get("page_number")),
        token_count=int(snapshot["token_count"]),
        source_url=cast(str | None, snapshot.get("source_url")),
        vector_id=cast(str | None, snapshot.get("vector_id")),
    )


def _snapshot_chunks(snapshot: dict[str, object]) -> list[DocumentChunkRecord]:
    return [_record_from_snapshot(item) for item in cast(list[dict[str, object]], snapshot.get("chunks", []))]


async def _vector_call(operation, *args):  # type: ignore[no-untyped-def]
    """Run a vector operation to completion even if its caller is cancelled."""
    task = asyncio.create_task(asyncio.to_thread(operation, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with suppress(BaseException):
            await asyncio.shield(task)
        raise


async def _restore_index(request: Request, snapshot: dict[str, object], current_ids: list[str]) -> bool:
    """Restore vectors from the durable snapshot, preserving IDs shared with new data."""
    document_id = str(snapshot["id"])
    repository_id = str(snapshot["repository_id"])
    old_chunks = _snapshot_chunks(snapshot)
    old_ids = [chunk.vector_id or chunk.id for chunk in old_chunks]
    shared_ids = _document_store(request).shared_vector_ids(document_id, current_ids)
    new_owned = [
        identifier
        for identifier in current_ids
        if identifier not in old_ids and identifier not in shared_ids
    ]
    vector_store = _vector_store(request)
    complete = True
    if new_owned:
        try:
            await _vector_call(vector_store.delete, repository_id, new_owned)
        except BaseException:  # noqa: BLE001 - compensation must survive cancellation
            complete = False
    if old_chunks:
        try:
            provider = _embedding_provider(request)
            readiness = await provider.ensure_ready()
            if readiness.state != "ready":
                raise DomainError("INDEX_FAILED", "嵌入模型不可用", 503, True)
            embeddings = await provider.embed_documents([chunk.text for chunk in old_chunks])
            await _vector_call(
                vector_store.upsert,
                repository_id,
                old_ids,
                [chunk.text for chunk in old_chunks],
                embeddings,
                [
                    {
                        "doc_id": document_id,
                        "doc_title": str(snapshot["title"]),
                        "section_path": chunk.section_path,
                        "source_url": chunk.source_url,
                        "chunk_index": chunk.chunk_index,
                        "source_type": snapshot.get("source_type", "yuque"),
                        "page_number": chunk.page_number,
                    }
                    for chunk in old_chunks
                ],
            )
        except BaseException:  # noqa: BLE001 - compensation must survive cancellation
            complete = False
    return complete


async def _index(
    request: Request,
    document: DocumentRecord,
    content: str,
    *,
    old_snapshot: dict[str, object] | None = None,
    mutation_id: str | None = None,
) -> DocumentRecord:
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
    old_chunk_snapshot = old_snapshot or {
        "id": document.id,
        "repository_id": document.repository_id,
        "title": document.title,
        "source_type": document.source_type,
        "chunks": [_chunk_snapshot(chunk) for chunk in old_chunks],
    }
    old_ids = [chunk.vector_id or chunk.id for chunk in old_chunks]
    new_ids = [record.id for record in records]
    vector_store = _vector_store(request)
    if mutation_id is not None:
        _update_intent(request, mutation_id, phase="indexing", new_vector_ids=new_ids)
    try:
        await _vector_call(
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
            await _vector_call(vector_store.delete, document.repository_id, stale_ids)
    except BaseException as error:
        # Restore SQLite first, then vectors. Shared IDs are never deleted; they are
        # re-upserted from the old snapshot to restore metadata exactly.
        with suppress(BaseException):
            _document_store(request).replace_chunks(document.id, _snapshot_chunks(old_chunk_snapshot))
        restored = await _restore_index(request, old_chunk_snapshot, new_ids)
        if not restored:
            raise DomainError("INDEX_FAILED", "写入文档索引失败，等待恢复", 503, True) from error
        if isinstance(error, asyncio.CancelledError):
            raise
        if isinstance(error, DomainError):
            raise
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


async def _await_shielded(awaitable):  # type: ignore[no-untyped-def]
    task = asyncio.create_task(awaitable)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with suppress(BaseException):
            await asyncio.shield(task)
        raise


def _intent_payload(record) -> dict[str, object]:  # type: ignore[no-untyped-def]
    try:
        payload = json.loads(record.payload_json)
    except (TypeError, json.JSONDecodeError):
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _update_intent(request: Request, mutation_id: str, **updates: object) -> dict[str, object]:
    record = _mutation_store(request).get(mutation_id)
    if record is None:
        raise DomainError("MUTATION_NOT_FOUND", "文档变更意图不存在", 409)
    payload = _intent_payload(record)
    payload.update(updates)
    _mutation_store(request).update(mutation_id, payload=payload)
    return payload


def _restore_file(snapshot: dict[str, object]) -> None:
    path_value = snapshot.get("markdown_path")
    if not isinstance(path_value, str) or not path_value:
        return
    path = Path(path_value)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    content = snapshot.get("content", "")
    # Compensation intentionally uses a direct write fallback: staged os.replace
    # may itself be the injected failing boundary.
    path.write_text(str(content), encoding="utf-8")


async def _compensate_mutation(request: Request, mutation_id: str) -> bool:
    record = _mutation_store(request).get(mutation_id)
    if record is None:
        return True
    payload = _intent_payload(record)
    payload["phase"] = "rollback"
    _mutation_store(request).update(mutation_id, payload=payload)
    complete = True
    remote_applied = bool(payload.get("remote_applied"))
    remote_id = payload.get("remote_id")
    if isinstance(remote_id, str) and (remote_applied or record.operation == "update"):
        try:
            if record.operation == "create":
                await _await_shielded(_gateway(request).delete_document(remote_id))
            else:
                old = cast(dict[str, object], payload.get("old_snapshot") or {})
                await _await_shielded(
                    _gateway(request).update_document(
                        UpdateYuqueDocumentRequest(
                            document_id=remote_id,
                            title=str(old.get("title", "")),
                            content=str(old.get("content", "")),
                        )
                    )
                )
        except BaseException:  # noqa: BLE001 - preserve intent across every failure
            complete = False
    old_snapshot = payload.get("old_snapshot")
    document_id = record.document_id or payload.get("document_id")
    if record.operation == "create" or not isinstance(old_snapshot, dict):
        if isinstance(document_id, str):
            current_ids = list(cast(list[str], payload.get("new_vector_ids") or []))
            if not current_ids:
                current_ids = _document_store(request).vector_ids(document_id)
            if current_ids:
                try:
                    await _vector_call(_vector_store(request).delete, record.repository_id, current_ids)
                except BaseException:  # noqa: BLE001 - preserve intent across every failure
                    complete = False
            try:
                current = _document_store(request).get(document_id)
                path_value = (
                    current.markdown_path
                    if current is not None and current.markdown_path
                    else str(request.app.state.settings.documents_dir / document_id / "document.md")
                )
                if path_value:
                    with suppress(FileNotFoundError):
                        Path(path_value).unlink()
                _document_store(request).delete_local(document_id)
            except BaseException:  # noqa: BLE001 - preserve intent across every failure
                complete = False
    else:
        try:
            _restore_file(old_snapshot)
        except BaseException:  # noqa: BLE001 - preserve intent across every failure
            complete = False
        try:
            _document_store(request).restore_snapshot(old_snapshot, _snapshot_chunks(old_snapshot))
        except BaseException:  # noqa: BLE001 - preserve intent across every failure
            complete = False
        try:
            current_ids = list(cast(list[str], payload.get("new_vector_ids") or []))
            if await _restore_index(request, old_snapshot, current_ids) is False:
                complete = False
        except BaseException:  # noqa: BLE001 - preserve intent across every failure
            complete = False
    if complete:
        _mutation_store(request).delete(mutation_id)
    return complete


async def recover_document_mutations(app) -> int:  # type: ignore[no-untyped-def]
    """Retry every pending API compensation at startup and on demand."""
    request = Request({"type": "http", "app": app})
    recovered = 0
    for record in _mutation_store(request).list():
        try:
            if await _compensate_mutation(request, record.id):
                recovered += 1
        except BaseException:  # noqa: BLE001, S112 - keep durable intent for next retry
            # Keep the intent durable for the next request/startup. Cancellation
            # cannot make a partially compensated mutation disappear.
            continue
    return recovered


async def _recover_for_request(request: Request) -> None:
    await recover_document_mutations(request.app)


@router.get("/api/repositories/{repository_id}/documents", response_model=list[DocumentSummary])
async def list_documents(request: Request, repository_id: str) -> list[DocumentSummary]:
    await _recover_for_request(request)
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
    await _recover_for_request(request)
    repository = _repository_store(request).get(repository_id)
    if repository is None or not repository.yuque_id:
        raise _not_found()
    intent = _mutation_store(request).create(
        operation="create",
        repository_id=repository.id,
        document_id=None,
        payload={"phase": "remote_pending", "remote_applied": False},
    )
    try:
        remote = await _gateway(request).create_document(
            CreateYuqueDocumentRequest(
                repository_id=repository.yuque_id, title=body.title, content=body.content
            )
        )
        _update_intent(
            request,
            intent.id,
            phase="remote_applied",
            remote_applied=True,
            remote_id=remote.yuque_id,
            remote_url=remote.url,
            new_title=remote.title,
        )
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
        _mutation_store(request).update(intent.id, document_id=document.id)
        _update_intent(request, intent.id, document_id=document.id)
        indexed = await _index(
            request,
            document,
            body.content,
            old_snapshot=None,
            mutation_id=intent.id,
        )
        _update_intent(request, intent.id, phase="file_pending")
        _persist_content(request, indexed, body.content)
        _mutation_store(request).delete(intent.id)
    except BaseException as error:
        mutation = _mutation_store(request).get(intent.id)
        remote_was_applied = bool(mutation and _intent_payload(mutation).get("remote_applied"))
        await _compensate_mutation(request, intent.id)
        if isinstance(error, asyncio.CancelledError):
            raise
        if isinstance(error, DomainError):
            raise
        if not remote_was_applied:
            raise _remote_failure(error) from error
        raise DomainError("INDEX_FAILED", "写入文档索引失败", 503, True) from error
    persisted = _document_store(request).get(document.id)
    if persisted is None:
        raise _not_found()
    return _detail(persisted)


@router.get("/api/documents/{document_id}", response_model=DocumentDetail)
async def read_document(request: Request, document_id: str) -> DocumentDetail:
    await _recover_for_request(request)
    document = _document_store(request).get(document_id)
    if document is None:
        raise _not_found()
    return _detail(document)


@router.put("/api/documents/{document_id}", response_model=DocumentDetail)
async def update_document(request: Request, document_id: str, body: DocumentInput) -> DocumentDetail:
    await _recover_for_request(request)
    document = _document_store(request).get(document_id)
    if document is None or not document.yuque_id:
        raise _not_found()
    old_snapshot = _document_snapshot(request, document)
    intent = _mutation_store(request).create(
        operation="update",
        repository_id=document.repository_id,
        document_id=document.id,
        payload={
            "phase": "remote_pending",
            "remote_applied": False,
            "remote_id": document.yuque_id,
            "old_snapshot": old_snapshot,
            "new_title": body.title,
            "new_content": body.content,
        },
    )
    try:
        remote = await _gateway(request).update_document(
            UpdateYuqueDocumentRequest(document_id=document.yuque_id, title=body.title, content=body.content)
        )
        _update_intent(
            request,
            intent.id,
            phase="remote_applied",
            remote_applied=True,
            remote_id=remote.yuque_id,
            remote_url=remote.url,
        )
        document.title = remote.title
        document.yuque_url = remote.url
        indexed = await _index(
            request,
            document,
            body.content,
            old_snapshot=old_snapshot,
            mutation_id=intent.id,
        )
        indexed.title = remote.title
        _update_intent(request, intent.id, phase="file_pending")
        _persist_content(request, indexed, body.content)
        _mutation_store(request).delete(intent.id)
    except BaseException as error:
        mutation = _mutation_store(request).get(intent.id)
        remote_was_applied = bool(mutation and _intent_payload(mutation).get("remote_applied"))
        await _compensate_mutation(request, intent.id)
        if isinstance(error, asyncio.CancelledError):
            raise
        if isinstance(error, DomainError):
            raise
        if not remote_was_applied:
            raise _remote_failure(error) from error
        raise DomainError("INDEX_FAILED", "写入文档索引失败", 503, True) from error
    return _detail(indexed)


@router.delete("/api/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(request: Request, document_id: str, body: DocumentDelete) -> None:
    await _recover_for_request(request)
    document = _document_store(request).get(document_id)
    if document is None:
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
