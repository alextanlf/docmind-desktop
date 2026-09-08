from __future__ import annotations

import asyncio
import json
import os
from base64 import b64decode, b64encode
from contextlib import suppress
from hashlib import sha256
from pathlib import Path
from typing import cast
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import APIRouter, Request, Response, status

from app.api.errors import DomainError
from app.core.embedding import EmbeddingProvider
from app.document.chunker import SemanticChunker
from app.document.parser import DocumentParser
from app.schemas.documents import DocumentDelete, DocumentDetail, DocumentInput, DocumentSummary
from app.schemas.imports import DownloadedDocument
from app.schemas.sync import ConflictResolution
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
        remote_deleted=document.remote_deleted,
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


def _claim_mutation(app, mutation_id: str) -> bool:  # type: ignore[no-untyped-def]
    with app.state.document_mutation_registry_lock:
        if mutation_id in app.state.active_document_mutations:
            return False
        app.state.active_document_mutations.add(mutation_id)
        return True


def _release_mutation(app, mutation_id: str) -> None:  # type: ignore[no-untyped-def]
    with app.state.document_mutation_registry_lock:
        app.state.active_document_mutations.discard(mutation_id)


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
    file_state = "unconfigured"
    file_bytes = b""
    content = ""
    if document.markdown_path:
        try:
            file_bytes = Path(document.markdown_path).read_bytes()
        except FileNotFoundError:
            file_state = "missing"
        except OSError as error:
            raise DomainError(
                "DOCUMENT_FILE_UNREADABLE",
                "读取文档文件失败",
                503,
                True,
                "检查文件权限后重试",
            ) from error
        else:
            file_state = "present"
            try:
                content = file_bytes.decode("utf-8")
            except UnicodeDecodeError:
                # File rollback remains byte-exact even if the prior local file is
                # not valid Markdown. Remote rollback has no safe text equivalent.
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
        "file_state": file_state,
        "file_bytes_b64": b64encode(file_bytes).decode("ascii") if file_state == "present" else None,
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


def _unowned_vector_ids(request: Request, vector_ids: list[str]) -> list[str]:
    unique_ids = list(dict.fromkeys(vector_ids))
    owned_ids = _document_store(request).owned_vector_ids(unique_ids)
    return [identifier for identifier in unique_ids if identifier not in owned_ids]


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
    """Restore vectors from the durable snapshot without deleting active ownership."""
    document_id = str(snapshot["id"])
    repository_id = str(snapshot["repository_id"])
    old_chunks = _snapshot_chunks(snapshot)
    old_ids = [chunk.vector_id or chunk.id for chunk in old_chunks]
    new_owned = _unowned_vector_ids(request, current_ids)
    vector_store = _vector_store(request)
    complete = True
    if new_owned:
        try:
            await _vector_call(vector_store.delete, repository_id, new_owned)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - retain intent for retry
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
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - retain intent for retry
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
        deletable_stale_ids = _unowned_vector_ids(request, stale_ids)
        if deletable_stale_ids:
            await _vector_call(
                vector_store.delete, document.repository_id, deletable_stale_ids
            )
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


def _persist_content(
    request: Request,
    document: DocumentRecord,
    content: str,
    *,
    mutation_id: str,
) -> None:
    directory = request.app.state.settings.documents_dir / document.id
    markdown_path = directory / "document.md"
    staged_path = directory / f".document.{mutation_id}.stage"
    _update_intent(
        request,
        mutation_id,
        phase="file_staging",
        staged_path=str(staged_path),
        target_path=str(markdown_path),
    )
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with staged_path.open("wb") as handle:
        handle.write(content.encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())
    _update_intent(request, mutation_id, phase="file_replace_pending")
    os.replace(staged_path, markdown_path)
    _update_intent(request, mutation_id, phase="file_metadata_pending")
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


def _marked_create_content(content: str, marker: str) -> str:
    return f"{content}\n\n<!-- {marker} -->"


async def _delete_remote_idempotently(
    request: Request,
    *,
    repository_id: str,
    document_id: str,
) -> None:
    """Delete once, accepting NOT_FOUND only after a separate absence check."""
    gateway = _gateway(request)
    try:
        await gateway.delete_document(document_id, repository_id)
    except asyncio.CancelledError:
        raise
    except DomainError as error:
        if error.status_code != 404:
            raise
        if await gateway.document_exists(repository_id, document_id):
            raise


def _restore_file(snapshot: dict[str, object], target_path: str | None = None) -> None:
    path_value = snapshot.get("markdown_path")
    old_path = Path(path_value) if isinstance(path_value, str) and path_value else None
    current_path = Path(target_path) if target_path else None
    file_state = snapshot.get("file_state")
    if file_state == "present" and old_path is not None:
        encoded = snapshot.get("file_bytes_b64")
        if not isinstance(encoded, str):
            raise ValueError("missing file snapshot bytes")
        old_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Direct writes remain retryable from the durable byte snapshot even if
        # os.replace is the injected failing boundary.
        old_path.write_bytes(b64decode(encoded, validate=True))
    elif file_state in {"missing", "unconfigured"}:
        if old_path is not None:
            with suppress(FileNotFoundError):
                old_path.unlink()
    elif old_path is not None:
        # Backward-compatible restoration for intents written before byte-state
        # snapshots existed.
        old_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        old_path.write_text(str(snapshot.get("content", "")), encoding="utf-8")
    if current_path is not None and current_path != old_path:
        with suppress(FileNotFoundError):
            current_path.unlink()


def _cleanup_staged_file(payload: dict[str, object]) -> None:
    staged_path = payload.get("staged_path")
    if isinstance(staged_path, str) and staged_path:
        with suppress(FileNotFoundError):
            Path(staged_path).unlink()


async def _compensate_mutation_unshielded(request: Request, mutation_id: str) -> bool:
    record = _mutation_store(request).get(mutation_id)
    if record is None:
        return True
    payload = _intent_payload(record)
    payload["phase"] = "rollback"
    _mutation_store(request).update(mutation_id, payload=payload)
    complete = True
    remote_applied = bool(payload.get("remote_applied"))
    remote_id = payload.get("remote_id")
    remote_repository_id = payload.get("remote_repository_id")
    if record.operation == "create" and not isinstance(remote_id, str):
        marker = payload.get("marker")
        if isinstance(remote_repository_id, str) and isinstance(marker, str):
            try:
                discovered = await _await_shielded(
                    _gateway(request).find_document_by_marker(
                        remote_repository_id, marker
                    )
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - preserve uncertain create intent
                complete = False
            else:
                if discovered is None:
                    complete = False
                else:
                    remote_id = discovered.yuque_id
                    remote_applied = True
                    payload.update(
                        remote_id=remote_id,
                        remote_url=discovered.url,
                        remote_applied=True,
                    )
                    _mutation_store(request).update(mutation_id, payload=payload)
        else:
            complete = False
    if isinstance(remote_id, str) and (remote_applied or record.operation == "update"):
        try:
            if record.operation == "create":
                if not isinstance(remote_repository_id, str):
                    complete = False
                else:
                    await _await_shielded(
                        _delete_remote_idempotently(
                            request,
                            repository_id=remote_repository_id,
                            document_id=remote_id,
                        )
                    )
            else:
                old = cast(dict[str, object], payload.get("old_snapshot") or {})
                old_content = str(old.get("remote_content", old.get("content", "")))
                restore_remote = True
                try:
                    current = await _await_shielded(
                        _gateway(request).read_document(remote_id)
                    )
                    restore_remote = current.content != old_content
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - read-back is best-effort
                    complete = False
                if restore_remote:
                    await _await_shielded(
                        _gateway(request).update_document(
                            UpdateYuqueDocumentRequest(
                                document_id=remote_id,
                                title=str(old.get("remote_title", old.get("title", ""))),
                                content=old_content,
                            )
                        )
                    )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - preserve intent across every failure
            complete = False
    old_snapshot = payload.get("old_snapshot")
    document_id = record.document_id or payload.get("document_id")
    if record.operation == "create" or not isinstance(old_snapshot, dict):
        if isinstance(document_id, str):
            current_ids = list(cast(list[str], payload.get("new_vector_ids") or []))
            if not current_ids:
                current_ids = _document_store(request).vector_ids(document_id)
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
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - preserve intent across every failure
                complete = False
            deletable_ids = _unowned_vector_ids(request, current_ids)
            if deletable_ids:
                try:
                    await _vector_call(
                        _vector_store(request).delete,
                        record.repository_id,
                        deletable_ids,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - preserve intent across every failure
                    complete = False
    else:
        try:
            target_path = payload.get("target_path")
            _restore_file(
                old_snapshot,
                target_path if isinstance(target_path, str) else None,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - preserve intent across every failure
            complete = False
        try:
            _document_store(request).restore_snapshot(old_snapshot, _snapshot_chunks(old_snapshot))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - preserve intent across every failure
            complete = False
        try:
            current_ids = list(cast(list[str], payload.get("new_vector_ids") or []))
            if await _restore_index(request, old_snapshot, current_ids) is False:
                complete = False
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - preserve intent across every failure
            complete = False
    if complete:
        try:
            _cleanup_staged_file(payload)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - retain intent until stage cleanup converges
            complete = False
    if complete:
        _mutation_store(request).delete(mutation_id)
    return complete


async def _compensate_mutation(request: Request, mutation_id: str) -> bool:
    """Finish compensation even when the recovery caller is cancelled."""
    task = asyncio.create_task(_compensate_mutation_unshielded(request, mutation_id))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with suppress(BaseException):
            await asyncio.shield(task)
        raise


async def recover_document_mutations(app) -> int:  # type: ignore[no-untyped-def]
    """Retry every pending API compensation at startup and on demand."""
    request = Request({"type": "http", "app": app})
    recovered = 0
    for record in _mutation_store(request).list():
        if record.operation == "distillation_create":
            payload = _intent_payload(record)
            try:
                await save_distillation_document(
                    app,
                    repository_id=record.repository_id,
                    distillation_id=record.id,
                    title=str(payload.get("requested_title", "")),
                    content=str(payload.get("requested_content", "")),
                )
                recovered += 1
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001, S112 - retry on next recovery pass
                continue
            continue
        if not _claim_mutation(app, record.id):
            continue
        try:
            if await _compensate_mutation(request, record.id):
                recovered += 1
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001, S112 - keep durable intent for next retry
            # Keep the intent durable for the next request/startup. Cancellation
            # cannot make a partially compensated mutation disappear.
            continue
        finally:
            _release_mutation(app, record.id)
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
    mutation_id = str(uuid4())
    marker = f"docmind-mutation:{mutation_id}"
    intent = _mutation_store(request).create(
        mutation_id=mutation_id,
        operation="create",
        repository_id=repository.id,
        document_id=None,
        payload={
            "phase": "remote_pending",
            "remote_applied": False,
            "remote_repository_id": repository.yuque_id,
            "requested_title": body.title,
            "requested_content": body.content,
            "marker": marker,
        },
    )
    if not _claim_mutation(request.app, intent.id):  # pragma: no cover - UUID collision
        raise DomainError("MUTATION_CONFLICT", "文档变更正在进行", 409)
    try:
        remote = await _gateway(request).create_document(
            CreateYuqueDocumentRequest(
                repository_id=repository.yuque_id,
                title=body.title,
                content=_marked_create_content(body.content, marker),
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
        _persist_content(request, indexed, body.content, mutation_id=intent.id)
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
    finally:
        _release_mutation(request.app, intent.id)
    persisted = _document_store(request).get(document.id)
    if persisted is None:
        raise _not_found()
    return _detail(persisted)


async def save_distillation_document(
    app,
    *,
    repository_id: str,
    distillation_id: str,
    title: str,
    content: str,
) -> DocumentDetail:
    """Persist a Yuque distillation through the normal document/index workflow."""
    request = Request({"type": "http", "app": app})
    repository = _repository_store(request).get(repository_id)
    if repository is None or not repository.yuque_id:
        raise _not_found()
    marker = f"docmind-distillation:{distillation_id}"
    remote = await _gateway(request).find_document_by_marker(repository.yuque_id, marker)
    existing = next(
        (
            document
            for document in _document_store(request).list_for_repository(repository_id)
            if document.source_identity == f"distillation:{distillation_id}"
            or (remote is not None and document.yuque_id == remote.yuque_id)
        ),
        None,
    )
    if existing is not None and existing.status == "indexed":
        return _detail(existing)
    mutation_id = distillation_id
    intent = _mutation_store(request).get(mutation_id)
    if intent is None:
        intent = _mutation_store(request).create(
            mutation_id=mutation_id,
            operation="distillation_create",
            repository_id=repository_id,
            document_id=existing.id if existing else None,
            payload={
                "phase": "remote_pending",
                "remote_applied": remote is not None,
                "remote_repository_id": repository.yuque_id,
                "requested_title": title,
                "requested_content": content,
                "marker": marker,
                "remote_id": remote.yuque_id if remote else None,
                "remote_url": remote.url if remote else None,
            },
        )
    if not _claim_mutation(app, mutation_id):
        raise DomainError("MUTATION_CONFLICT", "文档变更正在进行", 409)
    try:
        if remote is None:
            remote = await _gateway(request).create_document(
                CreateYuqueDocumentRequest(
                    repository_id=repository.yuque_id,
                    title=title,
                    content=_marked_create_content(content, marker),
                )
            )
        _update_intent(request, mutation_id, phase="remote_applied", remote_applied=True, remote_id=remote.yuque_id, remote_url=remote.url)
        document = existing or _document_store(request).create(
            DocumentRecord(
                id=str(uuid4()), repository_id=repository_id, yuque_id=remote.yuque_id,
                title=remote.title, source_url=remote.url, source_type="yuque",
                status="uploaded", yuque_url=remote.url,
                source_identity=f"distillation:{distillation_id}",
            )
        )
        _mutation_store(request).update(mutation_id, document_id=document.id)
        _update_intent(request, mutation_id, document_id=document.id)
        indexed = await _index(request, document, content, old_snapshot=None, mutation_id=mutation_id)
        _update_intent(request, mutation_id, phase="file_pending")
        _persist_content(request, indexed, content, mutation_id=mutation_id)
        _mutation_store(request).delete(mutation_id)
        persisted = _document_store(request).get(document.id)
        if persisted is None:
            raise _not_found()
        return _detail(persisted)
    except BaseException:
        await _compensate_mutation(request, mutation_id)
        raise
    finally:
        _release_mutation(app, mutation_id)


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
    try:
        old_remote = await _gateway(request).read_document(document.yuque_id)
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001 - gateway failures map to a stable API error
        raise _remote_failure(error) from None
    old_snapshot.update(
        remote_title=old_remote.title,
        remote_content=old_remote.content,
    )
    marker = f"docmind-mutation:{uuid4()!s}"
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
            "marker": marker,
        },
    )
    if not _claim_mutation(request.app, intent.id):  # pragma: no cover - UUID collision
        raise DomainError("MUTATION_CONFLICT", "文档变更正在进行", 409)
    try:
        remote = await _gateway(request).update_document(
            UpdateYuqueDocumentRequest(
                document_id=document.yuque_id,
                title=body.title,
                content=_marked_create_content(body.content, marker),
            )
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
        _persist_content(request, indexed, body.content, mutation_id=intent.id)
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
    finally:
        _release_mutation(request.app, intent.id)
    return _detail(indexed)


@router.delete("/api/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(request: Request, document_id: str, body: DocumentDelete) -> None:
    await _recover_for_request(request)
    document = _document_store(request).get(document_id)
    if document is None:
        pending = next((item for item in _cleanup_store(request).list() if item.document_id == document_id), None)
        if pending is not None:
            if not body.confirm:
                raise DomainError("CONFIRMATION_REQUIRED", "请确认删除文档", 400)
            try:
                pending_ids = _unowned_vector_ids(
                    request, list(json.loads(pending.vector_ids_json))
                )
                if pending_ids:
                    await asyncio.to_thread(
                        _vector_store(request).delete,
                        pending.repository_id,
                        pending_ids,
                    )
                _cleanup_store(request).delete(pending.id)
            except Exception as error:
                raise DomainError("INDEX_FAILED", "清理文档索引失败", 503, True) from error
            return
        raise _not_found()
    if not body.confirm:
        raise DomainError("CONFIRMATION_REQUIRED", "请确认删除文档", 400)
    if not document.yuque_id:
        raise _not_found()
    repository = _repository_store(request).get(document.repository_id)
    if repository is None or not repository.yuque_id:
        raise _not_found()
    try:
        await _gateway(request).delete_document(document.yuque_id, repository.yuque_id)
    except Exception as error:  # noqa: BLE001 - gateway boundary maps all failures
        raise _remote_failure(error) from None
    vector_ids = _document_store(request).vector_ids(document.id)
    _document_store(request).delete_local(document.id)
    deletable_ids = _unowned_vector_ids(request, vector_ids)
    try:
        if deletable_ids:
            await asyncio.to_thread(
                _vector_store(request).delete, document.repository_id, deletable_ids
            )
    except Exception:  # noqa: BLE001 - local cleanup must continue after remote success
        _cleanup_store(request).create(
            document.repository_id, document.id, deletable_ids
        )


@router.post("/api/documents/{document_id}/conflict", status_code=status.HTTP_204_NO_CONTENT)
async def resolve_conflict(request: Request, document_id: str) -> Response:
    payload = await request.json()
    resolution = ConflictResolution(payload.get("resolution", ""))
    await request.app.state.conflict_service.resolve(document_id, resolution)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
