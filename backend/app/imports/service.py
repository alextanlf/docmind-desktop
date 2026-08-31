from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.api.errors import DomainError
from app.config import AppSettings
from app.core.embedding import EmbeddingProvider
from app.document.chunker import SemanticChunker
from app.document.parser import DocumentParser
from app.imports.events import EventType, ImportEventBroker
from app.schemas.imports import (
    DownloadedDocument,
    ImportCreateRequest,
    ImportJobView,
    ParsedDocument,
    SourcePreview,
    SourceRef,
)
from app.schemas.yuque import (
    CreateYuqueDocumentRequest,
    UpdateYuqueDocumentRequest,
    YuqueDocument,
)
from app.storage.models import (
    DocumentChunkRecord,
    DocumentRecord,
    ImportJobRecord,
    ImportStatus,
)
from app.storage.repositories import DocumentStore, ImportJobStore, RepositoryStore
from app.storage.vectorstore import PersistentVectorStore
from app.yuque.gateway import YuqueGateway


class ImportService:
    def __init__(
        self,
        *,
        settings: AppSettings,
        source_inspector: Any,
        parser: DocumentParser,
        chunker: SemanticChunker,
        embedding_provider: EmbeddingProvider,
        vector_store: PersistentVectorStore,
        yuque_gateway: YuqueGateway,
        repository_store: RepositoryStore,
        document_store: DocumentStore,
        job_store: ImportJobStore,
        event_broker: ImportEventBroker,
    ) -> None:
        self.settings = settings
        self.source_inspector = source_inspector
        self.parser = parser
        self.chunker = chunker
        self.embedding_provider = embedding_provider
        self.vector_store = vector_store
        self.yuque_gateway = yuque_gateway
        self.repository_store = repository_store
        self.document_store = document_store
        self.job_store = job_store
        self.event_broker = event_broker
        self._job_locks: dict[str, asyncio.Lock] = {}
        self._event_locks: dict[str, asyncio.Lock] = {}
        self._reservation_lock = asyncio.Lock()

    async def inspect(self, ref: SourceRef) -> SourcePreview:
        return await self.source_inspector.inspect(ref)

    async def create(self, request: ImportCreateRequest) -> ImportJobView:
        repository = self.repository_store.get(request.repository_id)
        if repository is None or not repository.yuque_id:
            raise DomainError("REPOSITORY_NOT_FOUND", "目标知识库不存在", 404)
        preview = await self.inspect(request.source)
        if preview.fingerprint != request.fingerprint:
            raise DomainError("SOURCE_CHANGED", "文档内容已变更，请重新预览", 409)

        async with self._reservation_lock:
            duplicate = self.document_store.find_by_hash(
                request.repository_id, request.fingerprint
            )
            if duplicate is not None and request.duplicate_decision is None:
                raise DomainError(
                    "DUPLICATE_DECISION_REQUIRED", "请选择跳过或更新重复文档", 409
                )
            if duplicate is None and request.duplicate_decision is not None:
                raise DomainError(
                    "DUPLICATE_DECISION_INVALID", "当前文档不是重复项，不需要重复处理决策", 409
                )

            job_record = ImportJobRecord(
                id=str(uuid4()),
                source_kind=request.source.kind,
                repository_id=request.repository_id,
                document_id=duplicate.id if duplicate is not None else None,
                message="等待导入",
            )
            job_record.source_value = _encode_source_metadata(
                value=request.source.value,
                fingerprint=request.fingerprint,
                duplicate_decision=request.duplicate_decision or "create",
                job_id=job_record.id,
            )
            job = self.job_store.reserve(job_record, fingerprint=request.fingerprint)
        return _job_view(job)

    def get(self, job_id: str) -> ImportJobView:
        return _job_view(self._job(job_id))

    async def run(self, job_id: str) -> None:
        lock = self._job_locks.setdefault(job_id, asyncio.Lock())
        if lock.locked():
            raise DomainError("IMPORT_ALREADY_RUNNING", "导入任务正在运行", 409)
        await lock.acquire()
        try:
            try:
                job = self._job(job_id)
                if job.state == ImportStatus.PENDING:
                    await self._transition(
                        job_id, {ImportStatus.PENDING}, ImportStatus.PARSING, 0, "正在解析文档"
                    )
                    job = self._job(job_id)
                if job.state == ImportStatus.PARSING:
                    if not await self._run_parsing(job_id):
                        return
                    job = self._job(job_id)
                if job.state == ImportStatus.UPLOADING:
                    if not await self._run_upload(job_id):
                        return
                    job = self._job(job_id)
                if job.state == ImportStatus.INDEXING:
                    await self._run_index(job_id)
            except asyncio.CancelledError:
                with suppress(DomainError):
                    await asyncio.shield(self._cancel_if_requested(job_id))
                raise
            except Exception:  # noqa: BLE001 - runner is the workflow's final error boundary
                await self._fail_unhandled(job_id)
        finally:
            lock.release()

    async def retry(self, job_id: str) -> ImportJobView:
        lock = self._job_locks.setdefault(job_id, asyncio.Lock())
        if lock.locked():
            raise DomainError("IMPORT_ALREADY_RUNNING", "导入任务正在运行", 409)
        await lock.acquire()
        try:
            job = self._job(job_id)
            metadata = _source_metadata(job)
            if not metadata["fingerprint"]:
                ref = SourceRef(kind=job.source_kind, value=metadata["value"])
                preview = await self.inspect(ref)
                duplicate = self.document_store.find_by_hash(
                    job.repository_id or "", preview.fingerprint
                )
                if job.document_id is None and duplicate is not None:
                    job = self.job_store.attach_document(job_id, duplicate.id)
                decision = "update" if job.document_id or duplicate is not None else "create"
                metadata = {
                    "value": ref.value,
                    "fingerprint": preview.fingerprint,
                    "duplicate_decision": decision,
                    "last_event_sequence": metadata["last_event_sequence"],
                    "stale_vector_ids": metadata["stale_vector_ids"],
                }
            self.job_store.update_source_value(
                job_id,
                _encode_source_metadata(
                    value=metadata["value"],
                    fingerprint=metadata["fingerprint"],
                    duplicate_decision=metadata["duplicate_decision"],
                    job_id=job_id,
                    last_event_sequence=metadata["last_event_sequence"],
                    stale_vector_ids=metadata["stale_vector_ids"],
                ),
            )
            job = self.job_store.reset_for_retry(job_id)
            await self.event_broker.reopen(job_id)
            return _job_view(job)
        finally:
            lock.release()

    async def cancel(self, job_id: str) -> ImportJobView:
        job = self.job_store.request_cancel(job_id)
        lock = self._job_locks.setdefault(job_id, asyncio.Lock())
        if job.state == ImportStatus.PENDING and not lock.locked():
            job = self.job_store.cancel(job_id)
            await self._publish_event(
                job_id, "done", {"progress": job.progress, "state": job.state.value}
            )
        return _job_view(job)

    async def ensure_terminal_event(self, job_id: str, job: ImportJobView) -> None:
        lock = self._event_locks.setdefault(job_id, asyncio.Lock())
        async with lock:
            if await self.event_broker.terminal(job_id) is not None:
                return
            if job.state == "failed":
                event_type: EventType = "error"
                payload = {
                    "progress": job.progress,
                    "state": job.state,
                    "code": job.error_code,
                    "message": job.error_message or job.message,
                    "retryable": job.retryable,
                }
            elif job.state in {"completed", "cancelled"}:
                event_type = "done"
                payload = {
                    "progress": job.progress,
                    "state": job.state,
                    "message": job.message,
                }
            else:
                return
            sequence = self.job_store.allocate_event_sequence(job_id)
            await self.event_broker.publish(
                job_id, event_type, payload, sequence=sequence
            )

    async def _run_parsing(self, job_id: str) -> bool:
        job = self._job(job_id)
        if await self._cancel_if_requested(job_id):
            return False
        metadata = _source_metadata(job)
        ref = SourceRef(kind=job.source_kind, value=metadata["value"])
        try:
            downloaded: DownloadedDocument = await self.source_inspector.load(ref)
        except DomainError as error:
            await self._fail(job_id, error.code, error.message, error.retryable)
            return False
        try:
            parsed = self.parser.parse(downloaded)
        except Exception:  # noqa: BLE001 - parser implementations cross a library boundary
            await self._fail(job_id, "PARSE_FAILED", "文档解析失败", False)
            return False
        if metadata["fingerprint"] != SourcePreview.from_document(
            ref.kind, downloaded
        ).fingerprint:
            await self._fail(job_id, "SOURCE_CHANGED", "文档内容已变更，请重新预览", False)
            return False

        await self._progress(job_id, ImportStatus.PARSING, 20, "文档解析完成")
        if await self._cancel_if_requested(job_id):
            return False
        if metadata["duplicate_decision"] != "skip":
            self._persist_parsed_document(job_id, downloaded, parsed)
        if await self._cancel_if_requested(job_id):
            return False
        await self._transition(
            job_id, {ImportStatus.PARSING}, ImportStatus.UPLOADING, 45, "准备写入语雀"
        )
        return True

    async def _run_upload(self, job_id: str) -> bool:
        job = self._job(job_id)
        metadata = _source_metadata(job)
        if await self._cancel_if_requested(job_id):
            return False
        if metadata["duplicate_decision"] != "skip":
            if job.document_id:
                document = self._document(job)
            else:
                document = self.document_store.find_by_hash(
                    job.repository_id or "", metadata["fingerprint"]
                )
                if document is None:
                    await self._fail(job_id, "UPLOAD_FAILED", "待上传文档不存在", True)
                    return False
            repository = self.repository_store.get(job.repository_id or "")
            if repository is None or not repository.yuque_id:
                await self._fail(job_id, "UPLOAD_FAILED", "目标知识库不可用", True)
                return False
            try:
                markdown = Path(document.markdown_path or "").read_text(encoding="utf-8")
                remote_markdown = f"{markdown.rstrip()}\n\n<!-- {metadata['marker']} -->\n"
                if document.yuque_id:
                    remote = await self.yuque_gateway.update_document(
                        UpdateYuqueDocumentRequest(
                            document_id=document.yuque_id,
                            title=document.title,
                            content=remote_markdown,
                        )
                    )
                else:
                    remote = await self._reconcile_remote_document(
                        job_id, repository.yuque_id, metadata["marker"]
                    )
                    if await self._cancel_if_requested(job_id):
                        return False
                    if remote is None:
                        if job.document_id is not None:
                            raise DomainError(
                                "UPLOAD_FAILED",
                                "已绑定文档缺少远端标识，为避免重复创建已停止",
                                409,
                                True,
                            )
                        if await self._cancel_if_requested(job_id):
                            return False
                        remote = await self.yuque_gateway.create_document(
                            CreateYuqueDocumentRequest(
                                repository_id=repository.yuque_id,
                                title=document.title,
                                content=remote_markdown,
                            )
                        )
                self.document_store.update_remote(
                    document.id, yuque_id=remote.yuque_id, yuque_url=remote.url
                )
                if job.document_id is None:
                    self.job_store.attach_document(job_id, document.id)
            except Exception:  # noqa: BLE001 - gateway failures map to a stable workflow code
                await self._fail(job_id, "UPLOAD_FAILED", "写入语雀失败", True)
                return False

        if await self._cancel_if_requested(job_id):
            return False

        await self._transition(
            job_id, {ImportStatus.UPLOADING}, ImportStatus.INDEXING, 70, "正在创建索引"
        )
        return True

    async def _run_index(self, job_id: str) -> None:
        job = self._job(job_id)
        metadata = _source_metadata(job)
        if await self._cancel_if_requested(job_id):
            return
        if metadata["duplicate_decision"] == "skip":
            await self._progress(job_id, ImportStatus.INDEXING, 90, "已跳过重复文档")
            if await self._cancel_if_requested(job_id):
                return
        else:
            document = self._document(job)
            try:
                parsed = self._parsed_from_persisted(document)
                chunks = self.chunker.chunk(parsed)
                status = await self.embedding_provider.ensure_ready()
                if status.state != "ready":
                    raise DomainError("INDEX_FAILED", "嵌入模型不可用", 503, True)
                embeddings = await self.embedding_provider.embed_documents(
                    [chunk.text for chunk in chunks]
                )
            except Exception:  # noqa: BLE001 - embedding implementations are externally supplied
                await self._fail(job_id, "INDEX_FAILED", "创建文档向量失败", True)
                return
            await self._progress(job_id, ImportStatus.INDEXING, 90, "向量生成完成")
            if await self._cancel_if_requested(job_id):
                return
            old_ids = self.document_store.vector_ids(document.id)
            records = self._chunk_records(job_id, document, chunks)
            new_ids = [record.vector_id or record.id for record in records]
            created_ids = [identifier for identifier in new_ids if identifier not in old_ids]
            stale_ids = list(
                dict.fromkeys(
                    identifier
                    for identifier in [*old_ids, *metadata["stale_vector_ids"]]
                    if identifier not in new_ids
                )
            )
            commit_state = {"sqlite_replaced": False}
            commit_started = False
            try:
                await self._vector_mutation(
                    self.vector_store.upsert,
                    job.repository_id,
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
                if self._job(job_id).cancel_requested:
                    await self._delete_vectors(job.repository_id or "", created_ids)
                    await self._cancel_if_requested(job_id)
                    return
                self.job_store.update_stale_vector_ids(job_id, stale_ids)
                commit_started = True
                await self._commit_index(
                    document.id,
                    records,
                    job.repository_id or "",
                    stale_ids,
                    commit_state,
                )
                self.job_store.update_stale_vector_ids(job_id, [])
                if await self._cancel_if_requested(job_id):
                    return
            except asyncio.CancelledError:
                if self._job(job_id).cancel_requested and not commit_state["sqlite_replaced"]:
                    await self._delete_vectors(job.repository_id or "", created_ids)
                raise
            except Exception:  # noqa: BLE001 - storage backends map to a stable workflow code
                if not commit_started or not commit_state["sqlite_replaced"]:
                    await self._delete_vectors(job.repository_id or "", created_ids)
                await self._fail(job_id, "INDEX_FAILED", "写入文档索引失败", True)
                return

        completed = self.job_store.transition(
            job_id,
            expected={ImportStatus.INDEXING},
            target=ImportStatus.COMPLETED,
            progress=100,
            message="导入完成",
        )
        await self._publish_event(
            job_id,
            "done",
            {"progress": 100, "state": completed.state.value, "message": completed.message},
        )

    async def _reconcile_remote_document(
        self, job_id: str, repository_id: str, marker: str
    ) -> YuqueDocument | None:
        marker_comment = f"<!-- {marker} -->"
        candidates = await self.yuque_gateway.list_documents(repository_id)
        if self._job(job_id).cancel_requested:
            return None
        for candidate in candidates:
            content = await self.yuque_gateway.read_document(candidate.yuque_id)
            if self._job(job_id).cancel_requested:
                return None
            if marker_comment in content.content:
                return candidate
        return None

    async def _vector_mutation(self, operation: Any, *args: Any) -> None:
        mutation = asyncio.create_task(asyncio.to_thread(operation, *args))
        try:
            await asyncio.shield(mutation)
        except asyncio.CancelledError:
            with suppress(Exception):
                await mutation
            raise

    async def _commit_index(
        self,
        document_id: str,
        records: list[DocumentChunkRecord],
        repository_id: str,
        stale_ids: list[str],
        state: dict[str, bool],
    ) -> None:
        async def commit() -> None:
            await asyncio.to_thread(self.document_store.replace_chunks, document_id, records)
            state["sqlite_replaced"] = True
            if not stale_ids:
                return
            for attempt in range(2):
                try:
                    await asyncio.to_thread(self.vector_store.delete, repository_id, stale_ids)
                    return
                except Exception:
                    if attempt == 1:
                        raise

        mutation = asyncio.create_task(commit())
        try:
            await asyncio.shield(mutation)
        except asyncio.CancelledError:
            with suppress(Exception):
                await mutation
            raise

    async def _delete_vectors(self, repository_id: str, vector_ids: list[str]) -> None:
        with suppress(Exception):
            await self._vector_mutation(self.vector_store.delete, repository_id, vector_ids)

    def _persist_parsed_document(
        self, job_id: str, downloaded: DownloadedDocument, parsed: ParsedDocument
    ) -> None:
        job = self._job(job_id)
        metadata = _source_metadata(job)
        existing = self.document_store.find_by_hash(
            job.repository_id or "", metadata["fingerprint"]
        )
        document_id = job.document_id or (existing.id if existing is not None else str(uuid4()))
        directory = self.settings.documents_dir / document_id
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        raw_path = directory / "raw.bin"
        markdown_path = directory / "document.md"
        raw_path.write_bytes(downloaded.raw_bytes)
        markdown_path.write_text(parsed.markdown, encoding="utf-8")
        if job.document_id is None and existing is None:
            self.document_store.create(
                DocumentRecord(
                    id=document_id,
                    repository_id=job.repository_id or "",
                    title=parsed.title,
                    source_url=parsed.source_url,
                    raw_path=str(raw_path),
                    markdown_path=str(markdown_path),
                    source_type="import",
                    content_hash=metadata["fingerprint"],
                )
            )
        else:
            self.document_store.update_import_metadata(
                document_id,
                title=parsed.title,
                source_url=parsed.source_url,
                raw_path=str(raw_path),
                markdown_path=str(markdown_path),
                content_hash=metadata["fingerprint"],
            )

    def _parsed_from_persisted(self, document: DocumentRecord) -> ParsedDocument:
        markdown = Path(document.markdown_path or "").read_bytes()
        return self.parser.parse(
            DownloadedDocument(
                title=document.title,
                source_url=document.source_url or "",
                media_type="text/markdown",
                raw_bytes=markdown,
                local_path=Path(document.markdown_path or ""),
            )
        )

    @staticmethod
    def _chunk_records(
        job_id: str, document: DocumentRecord, chunks: list[Any]
    ) -> list[DocumentChunkRecord]:
        records: list[DocumentChunkRecord] = []
        for chunk in chunks:
            digest = sha256(chunk.text.encode("utf-8")).hexdigest()
            identifier = str(
                uuid5(NAMESPACE_URL, f"{document.id}:{job_id}:{chunk.chunk_index}:{digest}")
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

    async def _transition(
        self,
        job_id: str,
        expected: set[ImportStatus],
        target: ImportStatus,
        progress: int,
        message: str,
    ) -> ImportJobRecord:
        job = self.job_store.transition(
            job_id,
            expected=expected,
            target=target,
            progress=progress,
            message=message,
        )
        await self._publish_event(
            job_id,
            "progress",
            {"progress": progress, "state": target.value, "message": message},
        )
        return job

    async def _progress(
        self, job_id: str, state: ImportStatus, progress: int, message: str
    ) -> ImportJobRecord:
        job = self.job_store.update_progress(
            job_id, expected=state, progress=progress, message=message
        )
        await self._publish_event(
            job_id,
            "progress",
            {"progress": progress, "state": state.value, "message": message},
        )
        return job

    async def _cancel_if_requested(self, job_id: str) -> bool:
        job = self._job(job_id)
        if not job.cancel_requested:
            return False
        cancelled = self.job_store.cancel(job_id)
        await self._publish_event(
            job_id,
            "done",
            {
                "progress": cancelled.progress,
                "state": cancelled.state.value,
                "message": cancelled.message,
            },
        )
        return True

    async def _fail(self, job_id: str, code: str, message: str, retryable: bool) -> None:
        failed = self.job_store.fail(
            job_id, code=code, message=message, retryable=retryable
        )
        await self._publish_event(
            job_id,
            "error",
            {
                "progress": failed.progress,
                "state": failed.state.value,
                "code": code,
                "message": message,
                "retryable": retryable,
            },
        )

    async def _fail_unhandled(self, job_id: str) -> None:
        job = self._job(job_id)
        if job.state not in {
            ImportStatus.PARSING,
            ImportStatus.UPLOADING,
            ImportStatus.INDEXING,
        }:
            return
        if job.cancel_requested:
            await self._cancel_if_requested(job_id)
            return
        failures = {
            ImportStatus.PARSING: ("PARSE_FAILED", "文档解析失败", False),
            ImportStatus.UPLOADING: ("UPLOAD_FAILED", "写入语雀失败", True),
            ImportStatus.INDEXING: ("INDEX_FAILED", "写入文档索引失败", True),
        }
        code, message, retryable = failures[job.state]
        await self._fail(job_id, code, message, retryable)

    async def _publish_event(
        self, job_id: str, event_type: EventType, payload: dict[str, Any]
    ) -> None:
        lock = self._event_locks.setdefault(job_id, asyncio.Lock())
        async with lock:
            sequence = self.job_store.allocate_event_sequence(job_id)
            await self.event_broker.publish(
                job_id, event_type, payload, sequence=sequence
            )

    def _job(self, job_id: str) -> ImportJobRecord:
        job = self.job_store.get(job_id)
        if job is None:
            raise DomainError("IMPORT_NOT_FOUND", "导入任务不存在", 404)
        return job

    def _document(self, job: ImportJobRecord) -> DocumentRecord:
        document = self.document_store.get(job.document_id or "")
        if document is None:
            raise DomainError("IMPORT_STATE_CONFLICT", "导入文档不存在", 409)
        return document


def _job_view(job: ImportJobRecord) -> ImportJobView:
    metadata = _source_metadata(job)
    return ImportJobView(
        id=job.id,
        source=SourceRef(kind=job.source_kind, value=metadata["value"]),
        repository_id=job.repository_id or "",
        state=job.state.value,
        current_stage=job.current_stage,
        progress=job.progress,
        message=job.message,
        error_code=job.error_code,
        error_message=job.error_message,
        retryable=job.retryable,
        document_id=job.document_id,
        cancel_requested=job.cancel_requested,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        updated_at=job.updated_at,
    )


def _encode_source_metadata(
    *,
    value: str,
    fingerprint: str,
    duplicate_decision: str,
    job_id: str,
    last_event_sequence: int = 0,
    stale_vector_ids: list[str] | None = None,
) -> str:
    return json.dumps(
        {
            "version": 2,
            "value": value,
            "fingerprint": fingerprint,
            "duplicate_decision": duplicate_decision,
            "upload_intent": {"marker": f"docmind-import:{job_id}"},
            "last_event_sequence": last_event_sequence,
            "stale_vector_ids": stale_vector_ids or [],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _source_metadata(job: ImportJobRecord) -> dict[str, Any]:
    try:
        metadata = json.loads(job.source_value)
    except (json.JSONDecodeError, TypeError):
        metadata = None
    if (
        isinstance(metadata, dict)
        and metadata.get("version") in {1, 2}
        and isinstance(metadata.get("value"), str)
        and isinstance(metadata.get("fingerprint"), str)
        and isinstance(metadata.get("duplicate_decision"), str)
    ):
        marker = f"docmind-import:{job.id}"
        intent = metadata.get("upload_intent")
        if isinstance(intent, dict) and isinstance(intent.get("marker"), str):
            marker = intent["marker"]
        last_event_sequence = metadata.get("last_event_sequence", 0)
        if (
            not isinstance(last_event_sequence, int)
            or isinstance(last_event_sequence, bool)
            or last_event_sequence < 0
        ):
            last_event_sequence = 0
        stale_vector_ids = metadata.get("stale_vector_ids", [])
        if not isinstance(stale_vector_ids, list) or not all(
            isinstance(identifier, str) for identifier in stale_vector_ids
        ):
            stale_vector_ids = []
        return {
            "value": metadata["value"],
            "fingerprint": metadata["fingerprint"],
            "duplicate_decision": metadata["duplicate_decision"],
            "marker": marker,
            "last_event_sequence": last_event_sequence,
            "stale_vector_ids": stale_vector_ids,
        }
    return {
        "value": job.source_value,
        "fingerprint": "",
        "duplicate_decision": "create",
        "marker": f"docmind-import:{job.id}",
        "last_event_sequence": 0,
        "stale_vector_ids": [],
    }
