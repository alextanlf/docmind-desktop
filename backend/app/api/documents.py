from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from base64 import b64decode, b64encode
from contextlib import suppress
from hashlib import sha256
from pathlib import Path
from typing import cast
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import FileResponse
from pydantic import ValidationError

from app.api.errors import DomainError
from app.core.embedding import EmbeddingProvider, require_ready_embedding
from app.document.chunker import SemanticChunker
from app.document.parser import DocumentParser
from app.document.path_tokens import FILE_DOMAIN, path_token
from app.document.sources import load_stored_original
from app.remote.provider import RemoteProvider
from app.remote.registry import ProviderRegistry
from app.schemas.documents import (
    ArchivedDocumentView,
    DocumentArchiveRequest,
    DocumentArchiveResult,
    DocumentDelete,
    DocumentDetail,
    DocumentInput,
    DocumentRemovalResult,
    DocumentRemoveRequest,
    DocumentSummary,
    LinkedOriginalInput,
    OriginalLocation,
    RemovedDocumentRestoreResult,
    RemovedDocumentView,
)
from app.schemas.imports import DownloadedDocument, ParsedFigure
from app.schemas.remote import (
    CreateRemoteDocumentRequest,
    RemoteDocument,
    UpdateRemoteDocumentRequest,
)
from app.schemas.sync import ConflictResolutionInput
from app.schemas.versioning import DocumentVersion
from app.storage.models import DocumentChunkRecord, DocumentRecord, RepositoryRecord
from app.storage.repositories import (
    DocumentMutationStore,
    DocumentStore,
    RemovedRemoteDocumentStore,
    RepositoryStore,
    VectorCleanupStore,
)
from app.storage.vectorstore import PersistentVectorStore

router = APIRouter(tags=["documents"])

_PDF_MAGIC = b"%PDF-"

#: 插图文件名白名单：64 位十六进制摘要 + 容器后缀，与 `ParsedFigure.asset` 的生成规则一致。
#:
#: 允许 png 与 jpeg：纯扫描页的内嵌位图多是已经压缩过的 JPEG，原样存比转 PNG 又快又小
#: （实测 66 ms / 216 KB 对 72 ms / 437 KB）。`content-type` 由后缀决定，不靠猜。
_ASSET_NAME = re.compile(r"[0-9a-f]{64}\.(?:png|jpe?g)")
_ASSET_MEDIA_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}


def _registry(request: Request) -> ProviderRegistry:
    return cast(ProviderRegistry, request.app.state.remote_registry)


def _provider(request: Request, repository: RepositoryRecord) -> RemoteProvider:
    """Resolve the remote provider a repository is bound to."""
    if not repository.provider:
        raise DomainError("REMOTE_NOT_BOUND", "知识库未绑定远程来源", 409, False)
    return _registry(request).get(repository.provider)


def _provider_for_id(request: Request, repository_id: str) -> RemoteProvider:
    repository = _repository_store(request).get(repository_id)
    if repository is None or not repository.provider:
        raise DomainError("REMOTE_NOT_BOUND", "知识库未绑定远程来源", 409, False)
    return _registry(request).get(repository.provider)


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


def _removed_document_store(request: Request) -> RemovedRemoteDocumentStore:
    return cast(RemovedRemoteDocumentStore, request.app.state.removed_document_store)


def _not_found() -> DomainError:
    return DomainError("NOT_FOUND", "资源不存在", 404)


def _summary(document: DocumentRecord, remote: RemoteDocument | None = None) -> DocumentSummary:
    return DocumentSummary(
        id=document.id,
        repository_id=document.repository_id,
        remote_id=document.remote_id,
        title=remote.title if remote is not None else document.title,
        remote_url=remote.url if remote is not None else document.remote_url,
        path=list(json.loads(document.remote_path_json or "[]")),
        chunk_count=document.chunk_count,
        status=document.status,
        remote_deleted=document.remote_deleted,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


def original_path(document: DocumentRecord) -> Path | None:
    """原件视图该去哪个文件读字节。

    链接型（`source_path`）优先：用户磁盘上的那个文件就是原件，我们不留副本。
    否则回落到我们自己存的那份（`raw_path`）—— 已导入的拷贝型文档仍在用它。

    两处读取点（类型嗅探与 `/raw` 的 Range）都走这里。它们只关心"有没有字节可发"，
    不关心字节在哪 —— 这也是链接型几乎不需要改动原件视图的原因。
    """
    for value in (document.source_path, document.raw_path):
        if value:
            return Path(value)
    return None


def original_revision_of(path: Path) -> str | None:
    """一个路径的 `mtime_ns:size`；拿不到就返回 ``None``。

    变更检测用 mtime 而不是内容哈希：为一次状态查询去重算 100 MB 的哈希是不合理的。
    它的失真方向是安全的 —— 被 `touch` 只是多提醒一次"要重新转换吗"，
    而真正的正确性由重新转换那一步重新读原件保证。
    """
    try:
        info = path.stat()
    except OSError:
        return None
    return f"{info.st_mtime_ns}:{info.st_size}"


def original_state(document: DocumentRecord) -> str:
    """原件现在是什么状态（见 `DocumentDetail.original_state` 的取值说明）。

    在**读取时**算，不落库、不后台轮询：`stat` 是微秒级，而数据库里存一个"状态列"
    必然会在某个时刻过期 —— 用户在我们两次检查之间移走了文件，那个列就开始说谎。
    """
    path = original_path(document)
    if path is None:
        return "none"
    revision = original_revision_of(path)
    if revision is None:
        return "missing"
    if document.source_path is None:
        # 拷贝型：字节是我们自己管的，不会"被改"，只可能丢（或被外部删掉）。
        return "available"
    # revision 未记录（导入时就没 stat 到）也算无法确认 —— 不能假装它就是当初那一版。
    if document.original_revision != revision:
        return "changed"
    return "available"


def _original_info(document: DocumentRecord) -> tuple[str | None, int | None]:
    """探测原件是否可预览。

    只读前几个字节做类型嗅探：原本落盘的 `raw.bin` 不带扩展名与 media type
    （见 `imports/service.py`），所以类型只能从内容判断；链接型原件同样按内容判断，
    免得用户把一个 `.pdf` 改名成别的后缀就换了一种处理。

    任何 OSError 都当作「没有原件」——原件缺失不该让整篇文档读不出来。链接型尤其如此：
    用户随时可能把文件移走，那是正常状态，不是错误。
    """
    path = original_path(document)
    if path is None:
        return None, None
    try:
        with open(path, "rb") as handle:
            head = handle.read(len(_PDF_MAGIC))
        size = os.stat(path).st_size
    except OSError:
        return None, None
    if head == _PDF_MAGIC:
        return "application/pdf", size
    return "application/octet-stream", size


def _detail(document: DocumentRecord, figures: list[ParsedFigure] | None = None) -> DocumentDetail:
    """文档详情。

    ``figures`` 由调用方从仓储取好传进来，默认空：只有「读文档」这条路径需要完整插图
    清单，而每次写操作都去查一遍所有 chunk 的图片列是白花时间。默认空而不是缺省字段，
    是为了让「没传」和「这篇没有图」在接口上可区分。
    """
    try:
        content = Path(document.markdown_path or "").read_text(encoding="utf-8")
    except OSError:
        content = ""
    media_type, byte_size = _original_info(document)
    return DocumentDetail(
        **_summary(document).model_dump(),
        content=content,
        original_media_type=media_type,
        original_byte_size=byte_size,
        original_state=original_state(document),
        figures=figures or [],
    )


def _document_figures(request: Request, document_id: str) -> list[ParsedFigure]:
    """这篇文档的全部插图，按页码排序、同名去重。

    从 chunk 的 ``images_json`` 汇总，而不是另存一份文档级清单：图与 chunk 的对应关系才是
    检索时真正需要的那一份，再维护一份就会有第二份可能不一致的数据。

    单条记录解析失败时跳过它，而不是让整个文档读不出来 —— 一条坏记录不该让文档打不开。
    """
    figures: dict[str, ParsedFigure] = {}
    store = _document_store(request)
    for raw in store.chunk_image_payloads(document_id):
        if not raw or raw == "[]":
            continue
        try:
            entries = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                figure = ParsedFigure.model_validate(entry)
            except ValidationError:
                continue
            figures.setdefault(figure.asset, figure)
    return sorted(
        figures.values(),
        key=lambda figure: (figure.page_number or 0, figure.asset),
    )


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
        "remote_id": document.remote_id,
        "title": document.title,
        "source_url": document.source_url,
        "raw_path": document.raw_path,
        "markdown_path": document.markdown_path,
        "source_type": document.source_type,
        "content_hash": document.content_hash,
        "chunk_count": document.chunk_count,
        "status": document.status,
        "remote_url": document.remote_url,
        "created_at": document.created_at.isoformat() if document.created_at else None,
        "updated_at": document.updated_at.isoformat() if document.updated_at else None,
        "content": content,
        "file_state": file_state,
        "file_bytes_b64": b64encode(file_bytes).decode("ascii")
        if file_state == "present"
        else None,
        "chunks": [
            _chunk_snapshot(chunk) for chunk in _document_store(request).list_chunks(document.id)
        ],
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
    return [
        _record_from_snapshot(item)
        for item in cast(list[dict[str, object]], snapshot.get("chunks", []))
    ]


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


async def _restore_index(
    request: Request, snapshot: dict[str, object], current_ids: list[str]
) -> bool:
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
            await require_ready_embedding(provider)
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
                        "source_type": snapshot.get("source_type", "remote"),
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
            source_url=document.remote_url or document.source_url or "",
            media_type="text/markdown",
            raw_bytes=content.encode("utf-8"),
        )
    )
    chunks = _chunker(request).chunk(parsed)
    provider = _embedding_provider(request)
    await require_ready_embedding(provider)
    embeddings = await provider.embed_documents([chunk.text for chunk in chunks])
    records = [
        DocumentChunkRecord(
            id=str(
                uuid5(
                    NAMESPACE_URL,
                    f"{document.id}:{chunk.chunk_index}:{sha256(chunk.text.encode()).hexdigest()}",
                )
            ),
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
            await _vector_call(vector_store.delete, document.repository_id, deletable_stale_ids)
    except BaseException as error:
        # Restore SQLite first, then vectors. Shared IDs are never deleted; they are
        # re-upserted from the old snapshot to restore metadata exactly.
        with suppress(BaseException):
            _document_store(request).replace_chunks(
                document.id, _snapshot_chunks(old_chunk_snapshot)
            )
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
    mutation_id: str | None,
) -> None:
    directory = request.app.state.settings.documents_dir / document.id
    markdown_path = directory / "document.md"
    staged_path = directory / f".document.{mutation_id or uuid4()}.stage"
    if mutation_id is not None:
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
    if mutation_id is not None:
        _update_intent(request, mutation_id, phase="file_replace_pending")
    os.replace(staged_path, markdown_path)
    if mutation_id is not None:
        _update_intent(request, mutation_id, phase="file_metadata_pending")
    _document_store(request).update_editor(
        document.id,
        title=document.title,
        remote_id=document.remote_id,
        remote_url=document.remote_url,
        markdown_path=str(markdown_path),
        source_url=document.remote_url if document.remote_id else document.source_url,
    )


async def _create_local_document(
    request: Request, repository: RepositoryRecord, body: DocumentInput
) -> DocumentDetail:
    document = _document_store(request).create(
        DocumentRecord(
            id=str(uuid4()),
            repository_id=repository.id,
            title=body.title,
            source_type="local",
            status="pending",
        )
    )
    try:
        indexed = await _index(request, document, body.content, old_snapshot=None, mutation_id=None)
        _persist_content(request, indexed, body.content, mutation_id=None)
    except BaseException:
        vector_ids = _document_store(request).vector_ids(document.id)
        if vector_ids:
            try:
                await _vector_call(
                    _vector_store(request).delete, document.repository_id, vector_ids
                )
            except Exception:  # noqa: BLE001 - preserve cleanup for the next startup
                _cleanup_store(request).create(document.repository_id, document.id, vector_ids)
        _document_store(request).delete_local(document.id)
        _remove_local_files(request, document.id)
        raise
    persisted = _document_store(request).get(document.id)
    if persisted is None:  # pragma: no cover - document was just persisted
        raise _not_found()
    return _detail(persisted)


async def _update_local_document(
    request: Request, document: DocumentRecord, body: DocumentInput
) -> DocumentDetail:
    old_snapshot = _document_snapshot(request, document)
    request.app.state.version_store.snapshot(
        document.id, document.title, str(old_snapshot.get("content", ""))
    )
    document.title = body.title
    try:
        _persist_content(request, document, body.content, mutation_id=None)
        indexed = await _index(
            request, document, body.content, old_snapshot=old_snapshot, mutation_id=None
        )
    except BaseException:
        _document_store(request).restore_snapshot(old_snapshot, _snapshot_chunks(old_snapshot))
        _restore_file(old_snapshot)
        raise
    persisted = _document_store(request).get(indexed.id)
    if persisted is None:  # pragma: no cover - document was just persisted
        raise _not_found()
    return _detail(persisted)


def _remove_local_files(request: Request, document_id: str) -> None:
    directory = request.app.state.settings.documents_dir / document_id
    if directory.is_symlink():
        with suppress(FileNotFoundError):
            directory.unlink()
    elif directory.exists():
        with suppress(OSError):
            shutil.rmtree(directory)


def _remote_failure(error: Exception) -> DomainError:
    # 只认 DomainError 自带的 auth_expired 语义标记，不列举任何平台错误码——
    # 新增 provider 时只要在抛出点声明该标记，就会自动获得正确透传行为。
    if isinstance(error, DomainError) and error.auth_expired:
        return error
    status_code = error.status_code if isinstance(error, DomainError) else 503
    return DomainError(
        "REMOTE_OPERATION_FAILED", "远程文档操作失败", status_code, True, "重试远程操作"
    )


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
    provider: RemoteProvider,
    *,
    repository_id: str,
    document_id: str,
) -> None:
    """Delete once, accepting NOT_FOUND only after a separate absence check."""
    try:
        await provider.delete_document(document_id, repository_id)
    except asyncio.CancelledError:
        raise
    except DomainError as error:
        if error.status_code != 404:
            raise
        if await provider.document_exists(repository_id, document_id):
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
    provider = _provider_for_id(request, record.repository_id)
    remote_applied = bool(payload.get("remote_applied"))
    remote_id = payload.get("remote_id")
    remote_repository_id = payload.get("remote_repository_id")
    if record.operation == "create" and not isinstance(remote_id, str):
        marker = payload.get("marker")
        if isinstance(remote_repository_id, str) and isinstance(marker, str):
            try:
                discovered = await _await_shielded(
                    provider.find_document_by_marker(remote_repository_id, marker)
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - preserve uncertain create intent
                complete = False
            else:
                if discovered is None:
                    complete = False
                else:
                    remote_id = discovered.remote_id
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
                            provider,
                            repository_id=remote_repository_id,
                            document_id=remote_id,
                        )
                    )
            else:
                old = cast(dict[str, object], payload.get("old_snapshot") or {})
                old_content = str(old.get("remote_content", old.get("content", "")))
                restore_remote = True
                try:
                    current = await _await_shielded(provider.read_document(remote_id))
                    restore_remote = current.content != old_content
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - read-back is best-effort
                    complete = False
                if restore_remote:
                    await _await_shielded(
                        provider.update_document(
                            UpdateRemoteDocumentRequest(
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
    remote_documents: dict[str, RemoteDocument] = {}
    if repository.remote_id:
        remote_documents = {
            document.remote_id: document
            for document in await _provider(request, repository).list_documents(
                repository.remote_id
            )
        }
    return [
        _summary(document, remote_documents.get(document.remote_id or ""))
        for document in _document_store(request).list_for_repository(repository_id)
    ]


@router.post(
    "/api/repositories/{repository_id}/documents",
    response_model=DocumentDetail,
    status_code=status.HTTP_201_CREATED,
)
async def create_document(
    request: Request, repository_id: str, body: DocumentInput
) -> DocumentDetail:
    await _recover_for_request(request)
    repository = _repository_store(request).get(repository_id)
    if repository is None:
        raise _not_found()
    if not repository.remote_id:
        return await _create_local_document(request, repository, body)
    provider = _provider(request, repository)
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
            "remote_repository_id": repository.remote_id,
            "requested_title": body.title,
            "requested_content": body.content,
            "marker": marker,
        },
    )
    if not _claim_mutation(request.app, intent.id):  # pragma: no cover - UUID collision
        raise DomainError("MUTATION_CONFLICT", "文档变更正在进行", 409)
    try:
        remote = await provider.create_document(
            CreateRemoteDocumentRequest(
                repository_id=repository.remote_id,
                title=body.title,
                content=_marked_create_content(body.content, marker),
                parent_id=repository.remote_parent_id,
            )
        )
        _update_intent(
            request,
            intent.id,
            phase="remote_applied",
            remote_applied=True,
            remote_id=remote.remote_id,
            remote_url=remote.url,
            new_title=remote.title,
        )
        document = _document_store(request).create(
            DocumentRecord(
                id=str(uuid4()),
                repository_id=repository.id,
                remote_id=remote.remote_id,
                title=remote.title,
                source_url=remote.url,
                source_type=repository.provider or "remote",
                status="uploaded",
                remote_url=remote.url,
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
    """Persist a remote distillation through the normal document/index workflow."""
    request = Request({"type": "http", "app": app})
    repository = _repository_store(request).get(repository_id)
    if repository is None or not repository.remote_id:
        raise _not_found()
    provider = _provider(request, repository)
    marker = f"docmind-distillation:{distillation_id}"
    remote = await provider.find_document_by_marker(repository.remote_id, marker)
    existing = next(
        (
            document
            for document in _document_store(request).list_for_repository(
                repository_id, include_archived=True
            )
            if document.source_identity == f"distillation:{distillation_id}"
            or (remote is not None and document.remote_id == remote.remote_id)
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
                "remote_repository_id": repository.remote_id,
                "requested_title": title,
                "requested_content": content,
                "marker": marker,
                "remote_id": remote.remote_id if remote else None,
                "remote_url": remote.url if remote else None,
            },
        )
    if not _claim_mutation(app, mutation_id):
        raise DomainError("MUTATION_CONFLICT", "文档变更正在进行", 409)
    try:
        if remote is None:
            remote = await provider.create_document(
                CreateRemoteDocumentRequest(
                    repository_id=repository.remote_id,
                    title=title,
                    content=_marked_create_content(content, marker),
                    parent_id=repository.remote_parent_id,
                )
            )
        _update_intent(
            request,
            mutation_id,
            phase="remote_applied",
            remote_applied=True,
            remote_id=remote.remote_id,
            remote_url=remote.url,
        )
        document = existing or _document_store(request).create(
            DocumentRecord(
                id=str(uuid4()),
                repository_id=repository_id,
                remote_id=remote.remote_id,
                title=remote.title,
                source_url=remote.url,
                source_type=repository.provider or "remote",
                status="uploaded",
                remote_url=remote.url,
                source_identity=f"distillation:{distillation_id}",
            )
        )
        _mutation_store(request).update(mutation_id, document_id=document.id)
        _update_intent(request, mutation_id, document_id=document.id)
        indexed = await _index(
            request, document, content, old_snapshot=None, mutation_id=mutation_id
        )
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


@router.get("/api/documents/archived", response_model=list[ArchivedDocumentView])
async def list_archived_documents(request: Request) -> list[ArchivedDocumentView]:
    """所有知识库里已归档的文档，最近归档的在前。

    全局一份而不是按知识库分：这份列表住在设置页，用户在那里做的是"翻一遍我归档过
    什么、要不要放回去"，而不是"整理某个知识库"。跨库才能一眼看完。

    这条路由**必须声明在 ``/api/documents/{document_id}`` 之前**：否则
    ``archived`` 会被当成一篇文档的 id，返回一个"资源不存在"的 404。
    """
    names = {record.id: record.name for record in _repository_store(request).list()}
    return [
        ArchivedDocumentView(
            id=document.id,
            repository_id=document.repository_id,
            repository_name=names.get(document.repository_id, ""),
            title=document.title,
            remote_id=document.remote_id,
            archived_at=document.archived_at,
        )
        for document in _document_store(request).list_archived()
        if document.archived_at is not None
    ]


@router.get("/api/documents/{document_id}", response_model=DocumentDetail)
async def read_document(request: Request, document_id: str) -> DocumentDetail:
    await _recover_for_request(request)
    document = _document_store(request).get(document_id)
    if document is None:
        raise _not_found()
    return _detail(document, _document_figures(request, document_id))


@router.get("/api/documents/{document_id}/assets/{asset_name}", response_class=FileResponse)
async def read_document_asset(request: Request, document_id: str, asset_name: str) -> FileResponse:
    """返回一张插图的渲染图。

    文件名先过白名单正则再看路径是否落在文档自己的目录内，两道都做：正则可以挡住绝大多数
    构造，但「解析后仍在目录内」才是真正的边界 —— 只靠正则，等哪天规则放宽一个字符就成
    了任意文件读取。
    """
    await _recover_for_request(request)
    document = _document_store(request).get(document_id)
    if document is None:
        raise _not_found()
    if not _ASSET_NAME.fullmatch(asset_name):
        raise DomainError("DOCUMENT_ASSET_MISSING", "该插图不存在", 404, False)
    directory = (request.app.state.settings.documents_dir / document.id / "assets").resolve()
    path = (directory / asset_name).resolve()
    if not path.is_relative_to(directory) or not path.is_file():
        raise DomainError("DOCUMENT_ASSET_MISSING", "该插图不存在", 404, False)
    return FileResponse(
        path,
        media_type=_ASSET_MEDIA_TYPES.get(path.suffix.lstrip(".").lower(), "image/png"),
        content_disposition_type="inline",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/documents/{document_id}/raw", response_class=FileResponse)
async def read_document_original(request: Request, document_id: str) -> FileResponse:
    """流式返回导入时落盘的 PDF 原件。

    阅读器按需分页依赖 HTTP Range，这里直接交给 Starlette 的 FileResponse ——
    它已实现 206 / 416 / 多段 Range 与 `Accept-Ranges`，不要自己解析 Range 头。
    """
    await _recover_for_request(request)
    document = _document_store(request).get(document_id)
    if document is None:
        raise _not_found()
    media_type, _ = _original_info(document)
    path = original_path(document)
    if media_type is None or path is None or not path.is_file():
        raise DomainError("DOCUMENT_ORIGINAL_MISSING", "该文档没有可预览的原件", 404, False)
    return FileResponse(
        path,
        media_type=media_type,
        content_disposition_type="inline",
        headers={"Cache-Control": "no-store"},
    )


@router.put("/api/documents/{document_id}", response_model=DocumentDetail)
async def update_document(
    request: Request, document_id: str, body: DocumentInput
) -> DocumentDetail:
    await _recover_for_request(request)
    document = _document_store(request).get(document_id)
    if document is None:
        raise _not_found()
    if not document.remote_id:
        return await _update_local_document(request, document, body)
    provider = _provider_for_id(request, document.repository_id)
    old_snapshot = _document_snapshot(request, document)
    request.app.state.version_store.snapshot(
        document.id, document.title, str(old_snapshot.get("content", ""))
    )
    try:
        old_remote = await provider.read_document(document.remote_id)
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
            "remote_id": document.remote_id,
            "old_snapshot": old_snapshot,
            "new_title": body.title,
            "new_content": body.content,
            "marker": marker,
        },
    )
    if not _claim_mutation(request.app, intent.id):  # pragma: no cover - UUID collision
        raise DomainError("MUTATION_CONFLICT", "文档变更正在进行", 409)
    try:
        remote = await provider.update_document(
            UpdateRemoteDocumentRequest(
                document_id=document.remote_id,
                title=body.title,
                content=_marked_create_content(body.content, marker),
            )
        )
        _update_intent(
            request,
            intent.id,
            phase="remote_applied",
            remote_applied=True,
            remote_id=remote.remote_id,
            remote_url=remote.url,
        )
        document.title = remote.title
        document.remote_url = remote.url
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


async def _retry_pending_vector_cleanup(
    request: Request, document_id: str, *, confirm: bool
) -> bool:
    """这篇文档已经不在了，但它的向量还挂在清理队列里。重试清理。

    返回 False 表示"队列里也没有它" —— 调用方据此回 404。
    """
    pending = next(
        (item for item in _cleanup_store(request).list() if item.document_id == document_id),
        None,
    )
    if pending is None:
        return False
    if not confirm:
        raise DomainError("CONFIRMATION_REQUIRED", "请确认删除文档", 400)
    try:
        pending_ids = _unowned_vector_ids(request, list(json.loads(pending.vector_ids_json)))
        if pending_ids:
            await asyncio.to_thread(
                _vector_store(request).delete,
                pending.repository_id,
                pending_ids,
            )
        _cleanup_store(request).delete(pending.id)
    except Exception as error:
        raise DomainError("INDEX_FAILED", "清理文档索引失败", 503, True) from error
    return True


async def _remove_locally(request: Request, document: DocumentRecord) -> None:
    """把一篇文档移出 DocMind，**绝不碰远端**。

    远端文档要多写一条移除记录：同步的判重依据是"上次见到的远端状态"，所以一篇
    还在远端更新的文档会在被移除后自己回来（实测：远端未变时不回来，一变就回来）。
    """
    vector_ids = _document_store(request).vector_ids(document.id)
    if document.remote_id:
        _removed_document_store(request).add_many(
            document.repository_id, [(document.remote_id, document.title)]
        )
    _document_store(request).delete_local(document.id)
    _remove_local_files(request, document.id)
    deletable_ids = _unowned_vector_ids(request, vector_ids)
    if deletable_ids:
        try:
            await _vector_call(
                _vector_store(request).delete, document.repository_id, deletable_ids
            )
        except Exception:  # noqa: BLE001 - 本地清理仍可重试
            _cleanup_store(request).create(document.repository_id, document.id, deletable_ids)


async def _archive_document(request: Request, document: DocumentRecord) -> None:
    """归档一篇文档：内容一概不动，只写时间戳。

    远端文档要多写一条移出记录：同步的判重依据是"上次见到的远端状态"，所以一篇
    还在远端更新的文档会在归档后自己回来（与移除同源，实测：远端未变时不回来，
    一变就回来）。记录与时间戳不是同一个事务 —— 这与移除路径一样，代价是极端情况下
    可能只写了记录没有时间戳（表现为"同步不再拉它，但列表里还在"），重试一次即可；
    反过来先写时间戳再写记录则会漏掉同步跳过。
    """
    if document.remote_id:
        _removed_document_store(request).add_many(
            document.repository_id, [(document.remote_id, document.title)]
        )
    _document_store(request).archive(document.id)


@router.post("/api/documents/{document_id}/archive", status_code=status.HTTP_204_NO_CONTENT)
async def archive_document(request: Request, document_id: str) -> None:
    """把一篇文档归档。

    与永久删除分开是刻意的：归档可撤销、恢复即时（内容、向量、版本、图谱都还在），
    所以它不需要"输入标题"那种重确认；真正不可撤销的是删除，那条路的确认留在
    ``DELETE`` 上。
    """
    await _recover_for_request(request)
    document = _document_store(request).get(document_id)
    if document is None:
        raise _not_found()
    await _archive_document(request, document)


@router.post("/api/documents/{document_id}/unarchive", status_code=status.HTTP_204_NO_CONTENT)
async def unarchive_document(request: Request, document_id: str) -> None:
    """取消归档。

    远端文档还要删掉那条移出记录，否则同步会一直跳过它。

    与「恢复已移除的文档」不同，这里**不清同步快照**：文档行没被删过，快照描述的
    仍是"上次见到的远端状态"，留着它下次同步就能正确判断出"远端改过没改过"；
    清掉反而会让它被当成新文档重新下载并重新索引一遍。
    """
    await _recover_for_request(request)
    document = _document_store(request).get(document_id)
    if document is None:
        raise _not_found()
    _document_store(request).unarchive(document_id)
    if document.remote_id:
        _removed_document_store(request).discard_for_remote(
            document.repository_id, document.remote_id
        )


@router.delete("/api/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_document(request: Request, document_id: str, body: DocumentDelete) -> None:
    """把一篇文档**永久**移出 DocMind（只删本地）。

    远端知识库里的原文一律保留。想连远端一起删有单独的入口
    （``DELETE /api/documents/{id}/remote``），因为那件事不可撤销，不该是"删除"
    的默认含义 —— 之前正是如此，而确认框里一个字都没提，用户以为自己只是在
    本地清理。
    """
    await _recover_for_request(request)
    document = _document_store(request).get(document_id)
    if document is None:
        if await _retry_pending_vector_cleanup(request, document_id, confirm=body.confirm):
            return
        raise _not_found()
    if not body.confirm:
        raise DomainError("CONFIRMATION_REQUIRED", "请确认移除文档", 400)
    await _remove_locally(request, document)


@router.delete(
    "/api/documents/{document_id}/remote", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_document_remote(
    request: Request, document_id: str, body: DocumentDelete
) -> None:
    """删除文档**并且**删除远端原文。不可撤销，需要单独确认。

    与默认动作分开是刻意的：这条会真的删掉用户在语雀/飞书上的东西。
    """
    await _recover_for_request(request)
    document = _document_store(request).get(document_id)
    if document is None:
        if await _retry_pending_vector_cleanup(request, document_id, confirm=body.confirm):
            return
        raise _not_found()
    if not body.confirm:
        raise DomainError("CONFIRMATION_REQUIRED", "请确认删除远端原文", 400)
    if not document.remote_id:
        # 本地文档没有远端原文可删，走默认那条路，别假装删了远端。
        await _remove_locally(request, document)
        return
    repository = _repository_store(request).get(document.repository_id)
    if repository is None or not repository.remote_id:
        raise _not_found()
    try:
        await _provider(request, repository).delete_document(
            document.remote_id, repository.remote_id
        )
    except Exception as error:  # noqa: BLE001 - gateway boundary maps all failures
        raise _remote_failure(error) from None
    await _remove_locally(request, document)


@router.post(
    "/api/repositories/{repository_id}/documents/archive",
    response_model=DocumentArchiveResult,
)
async def archive_documents(
    request: Request, repository_id: str, body: DocumentArchiveRequest
) -> DocumentArchiveResult:
    """批量归档（默认动作）。

    一篇一个请求在几十篇时既慢又会在中途失败，留下"归档了一半"这种最难解释的结果；
    这里把所有远端身份一次登记完，再逐篇写时间戳。列表里不存在的 id 直接跳过，不报错
    —— 批量操作里"已经被别人删掉了"不该让整批失败。
    """
    await _recover_for_request(request)
    repository = _repository_store(request).get(repository_id)
    if repository is None:
        raise _not_found()
    store = _document_store(request)
    documents = [
        document
        for document_id in dict.fromkeys(body.document_ids)
        if (document := store.get(document_id)) is not None
        and document.repository_id == repository_id
    ]
    remote_pairs = [
        (document.remote_id, document.title) for document in documents if document.remote_id
    ]
    if remote_pairs:
        _removed_document_store(request).add_many(repository_id, remote_pairs)
    for document in documents:
        store.archive(document.id)
    return DocumentArchiveResult(archived=len(documents))


@router.post(
    "/api/repositories/{repository_id}/documents/remove",
    response_model=DocumentRemovalResult,
)
async def remove_documents(
    request: Request, repository_id: str, body: DocumentRemoveRequest
) -> DocumentRemovalResult:
    """批量把文档**永久**移出 DocMind（同样只删本地）。

    一篇一个请求在几十篇时既慢又会在中途失败，留下"移除了一半"这种最难解释的
    结果；这里在一个事务里登记移除记录，逐篇清本地。列表里不存在的 id 直接跳过，
    不报错 —— 批量操作里"已经被别人删掉了"不该让整批失败。
    """
    await _recover_for_request(request)
    repository = _repository_store(request).get(repository_id)
    if repository is None:
        raise _not_found()
    store = _document_store(request)
    documents = [
        document
        for document_id in dict.fromkeys(body.document_ids)
        if (document := store.get(document_id)) is not None
        and document.repository_id == repository_id
    ]
    # 先把所有远端身份一次登记完，再逐篇清本地：登记是廉价的，而少了任何一条，
    # 那篇文档都会在下次远端更新时自己回来。
    remote_pairs = [
        (document.remote_id, document.title)
        for document in documents
        if document.remote_id
    ]
    if remote_pairs:
        _removed_document_store(request).add_many(repository_id, remote_pairs)
    for document in documents:
        vector_ids = store.vector_ids(document.id)
        store.delete_local(document.id)
        _remove_local_files(request, document.id)
        deletable_ids = _unowned_vector_ids(request, vector_ids)
        if not deletable_ids:
            continue
        try:
            await _vector_call(
                _vector_store(request).delete, repository_id, deletable_ids
            )
        except Exception:  # noqa: BLE001 - 本地清理仍可重试
            _cleanup_store(request).create(repository_id, document.id, deletable_ids)
    return DocumentRemovalResult(removed=len(documents))


@router.get("/api/removed-documents", response_model=list[RemovedDocumentView])
async def list_all_removed_documents(request: Request) -> list[RemovedDocumentView]:
    """所有知识库里的「已移除、可重新同步」记录（旧流程留下的那批）。

    设置页那份「已归档」列表要能一并显示它们：这些远端文档是**旧版本**的移除动作
    留下的记录（文档行当时就被删了），合并进同一处才不会让它们变成没人找得到的
    孤儿 —— 同步会一直跳过它们，界面里却没有入口。

    这条路由声明在 ``/api/repositories/{repository_id}/removed-documents`` 之前是
    为了可读性，两者路径不同、不会互相遮蔽。
    """
    names = {record.id: record.name for record in _repository_store(request).list()}
    return [
        RemovedDocumentView(
            id=record.id,
            repository_id=record.repository_id,
            remote_id=record.remote_id,
            title=record.title,
            repository_name=names.get(record.repository_id, ""),
            removed_at=record.removed_at,
        )
        for record in _removed_document_store(request).list_orphaned()
    ]


@router.get(
    "/api/repositories/{repository_id}/removed-documents",
    response_model=list[RemovedDocumentView],
)
async def list_removed_documents(
    request: Request, repository_id: str
) -> list[RemovedDocumentView]:
    if _repository_store(request).get(repository_id) is None:
        raise _not_found()
    return [
        RemovedDocumentView(
            id=record.id,
            repository_id=record.repository_id,
            remote_id=record.remote_id,
            title=record.title,
            removed_at=record.removed_at,
        )
        for record in _removed_document_store(request).list_for_repository(repository_id)
    ]


@router.post(
    "/api/repositories/{repository_id}/removed-documents/{removal_id}/restore",
    response_model=RemovedDocumentRestoreResult,
)
async def restore_removed_document(
    request: Request, repository_id: str, removal_id: str
) -> RemovedDocumentRestoreResult:
    """恢复一篇被移除的远端文档：删掉移除记录，并允许它再次被同步。

    必须同时清掉 ``repository_sync_state`` 里那条陈旧快照。同步靠它判重，
    快照还在、远端内容又恰好没变过的话，之后那次同步会把它当 ``unchanged``
    跳过 —— 文档就永远回不来了（这一点实测过）。

    文档内容要由同步重新下载并重建索引（重活），所以这里只负责"允许它回来"，
    真正的拉回由调用方紧接着触发一次同步。
    """
    if _repository_store(request).get(repository_id) is None:
        raise _not_found()
    store = _removed_document_store(request)
    record = store.get(removal_id)
    if record is None or record.repository_id != repository_id:
        raise _not_found()
    discarded = store.discard(removal_id)
    if discarded is None:  # pragma: no cover - 上一行刚读到它
        raise _not_found()
    request.app.state.sync_state_store.discard(repository_id, discarded.remote_id)
    return RemovedDocumentRestoreResult(
        removal_id=removal_id,
        repository_id=repository_id,
        remote_id=discarded.remote_id,
        title=discarded.title,
    )


@router.post("/api/documents/{document_id}/conflict", status_code=status.HTTP_204_NO_CONTENT)
async def resolve_conflict(
    request: Request, document_id: str, payload: ConflictResolutionInput
) -> Response:
    await request.app.state.conflict_service.resolve(document_id, payload.resolution)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/api/documents/{document_id}/versions", response_model=list[DocumentVersion])
async def list_versions(request: Request, document_id: str) -> list[DocumentVersion]:
    return request.app.state.version_store.list(document_id)


@router.get("/api/documents/{document_id}/original-location", response_model=OriginalLocation)
async def original_location(request: Request, document_id: str) -> OriginalLocation:
    """原件在磁盘上的位置 + 一份主进程能验的签名。

    给"用系统程序打开原件"用：渲染层只发 documentId，主进程拿这个位置、验签之后
    `shell.openPath`。**签名不是装饰**：没有它，主进程就只是照着后端说的路径去打开文件，
    而"渲染层不能指名任意文件"这条性质会在主进程这一侧变成一个空壳（后端返回什么就打开
    什么，而渲染层能影响的恰恰是 documentId）。

    原件读不到时**不报错**：`state` 已经把"本来没有原件"和"绑定过但丢了"分开了，
    界面据此决定是给「用系统程序打开」还是「重新定位原件」。报 404 会让这两种情形
    在界面上长得一样，而那正是之前特意区分开的东西。
    """
    await _recover_for_request(request)
    document = _document_store(request).get(document_id)
    if document is None:
        raise _not_found()
    path = original_path(document)
    state = original_state(document)
    if path is None or state == "missing" or not path.is_file():
        return OriginalLocation(state="missing" if document.source_path else "none")
    secret = request.app.state.settings.session_token.get_secret_value()
    return OriginalLocation(
        path=str(path),
        path_token=path_token(secret, FILE_DOMAIN, str(path)),
        state=state,
    )


@router.post("/api/documents/{document_id}/original", response_model=DocumentDetail)
async def relink_original(
    request: Request, document_id: str, payload: LinkedOriginalInput
) -> DocumentDetail:
    """重新定位原件：把这篇文章重新绑到用户磁盘上的另一个路径。

    **只改绑定，不自动重新转换。** 转换是一次会动索引、动版本、可能动远端文档的重操作，
    应当由用户明确点"重新转换"来触发 —— 重新定位常常只是为了修一条断链（文件被挪走、
    或换了外接盘），那时内容一个字都没变。

    刻意**不更新** `original_revision`：绑定之后状态由"新文件的 mtime:size 是否等于当初
    记录的那一个"算出来。于是改名/移动（内容没变）会自动回到 available，而换成另一个
    文件则会显示 changed 并提示重新转换 —— 两种情形都不需要用户额外操作。
    """
    await _recover_for_request(request)
    document = _document_store(request).get(document_id)
    if document is None:
        raise _not_found()
    # 与导入同一条门：路径必须带主进程签名，否则渲染层就能把这篇文档指到任意文件上。
    loaded = request.app.state.import_service.source_inspector.linked_store.load(
        payload.path, payload.path_token, display_name=None
    )
    assert loaded.source_path is not None  # linked_store 一定带上它
    _document_store(request).update_linked_original(
        document.id, source_path=str(loaded.source_path)
    )
    refreshed = _document_store(request).get(document.id)
    if refreshed is None:  # pragma: no cover - 记录刚刚才读到过
        raise _not_found()
    return _detail(refreshed, _document_figures(request, document.id))


@router.post("/api/documents/{document_id}/reconvert", response_model=DocumentDetail)
async def reconvert_original(request: Request, document_id: str) -> DocumentDetail:
    """重新读取原件并转换一次（用户改了原件之后）。

    复用**编辑保存**那条流水线（`update_document`）：版本快照、向量替换、失败回滚、
    远端写回它都已经处理过。重新转换与"用户手改正文"在后果上是同一件事，
    另写一条流水线只会多出一份会漂移的补偿逻辑。
    """
    await _recover_for_request(request)
    document = _document_store(request).get(document_id)
    if document is None:
        raise _not_found()
    if not document.source_path:
        # 拷贝型没有"原件被改"这回事：那份字节是我们自己管的，只可能是丢失。
        raise DomainError(
            "DOCUMENT_ORIGINAL_NOT_LINKED", "这篇文档没有绑定磁盘上的原件，无法重新转换", 409, False
        )
    original = Path(document.source_path)
    if original_state(document) == "missing":
        # 用状态函数判断，而不是等下面的读取抛错：读不到原件与"这不是一个受支持的来源"
        # 是两件事 —— 前者用户能修（重新定位），后者不能，两者的原因码不该混。
        raise DomainError("DOCUMENT_ORIGINAL_MISSING", "原件已移动或删除，无法重新转换", 404, False)
    settings = request.app.state.settings
    try:
        loaded = load_stored_original(
            original,
            formats=request.app.state.format_registry,
            binary_max_bytes=settings.pdf_max_bytes,
            text_max_bytes=settings.html_markdown_max_bytes,
        )
        parsed = await asyncio.to_thread(request.app.state.document_parser.parse, loaded)
    except DomainError:
        raise
    except OSError:
        raise DomainError(
            "DOCUMENT_ORIGINAL_MISSING", "原件已移动或删除，无法重新转换", 404, False
        ) from None
    # 复用编辑保存那条流水线；返回的 detail 是"记下这一版之前"的状态，
    # 所以下面重新读一次记录再构造响应。
    await update_document(
        request, document_id, DocumentInput(title=parsed.title, content=parsed.markdown)
    )
    revision = original_revision_of(original)
    if revision is not None:
        # 记下这一次转换的是哪一版：状态随回 available，否则会一直提示"原件已更新"。
        _document_store(request).update_linked_original(
            document_id, source_path=str(original), original_revision=revision
        )
    refreshed = _document_store(request).get(document_id)
    if refreshed is None:  # pragma: no cover - 记录刚刚才更新过
        raise _not_found()
    return _detail(refreshed, _document_figures(request, document_id))
