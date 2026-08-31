from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.api.errors import DomainError
from app.config import AppSettings, EmbeddingSettings
from app.core.embedding import FakeEmbeddingProvider
from app.document.chunker import SemanticChunker
from app.document.parser import DocumentParser
from app.imports.events import InMemoryEventBroker
from app.imports.service import ImportService
from app.schemas.imports import (
    DownloadedDocument,
    ImportCreateRequest,
    SourcePreview,
    SourceRef,
)
from app.schemas.yuque import YuqueDocument, YuqueDocumentContent
from app.storage.models import (
    DocumentChunkRecord,
    DocumentRecord,
    ImportJobRecord,
    ImportStatus,
    RepositoryRecord,
)
from app.storage.repositories import DocumentStore, ImportJobStore, RepositoryStore


class FakeSourceInspector:
    def __init__(self, raw_bytes: bytes = b"# Imported\n\nUseful text") -> None:
        self.document = DownloadedDocument(
            title="Imported.md",
            source_url="https://example.test/imported.md",
            media_type="text/markdown",
            raw_bytes=raw_bytes,
        )
        self.load_started: asyncio.Event | None = None
        self.release_load: asyncio.Event | None = None

    async def inspect(self, ref: SourceRef) -> SourcePreview:
        del ref
        return SourcePreview.from_document("url", self.document)

    async def load(self, ref: SourceRef) -> DownloadedDocument:
        del ref
        if self.load_started is not None:
            self.load_started.set()
        if self.release_load is not None:
            await self.release_load.wait()
        return self.document


class FakeVectorStore:
    def __init__(self, fail_count: int = 0, delete_fail_count: int = 0) -> None:
        self.fail_count = fail_count
        self.delete_fail_count = delete_fail_count
        self.upserts: list[dict[str, Any]] = []
        self.deletes: list[list[str]] = []
        self.ids: set[str] = set()
        self.on_upsert: Callable[[], None] | None = None
        self.upsert_started: threading.Event | None = None
        self.release_upsert: threading.Event | None = None

    def upsert(
        self,
        repository_id: str,
        ids: list[str],
        texts: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
    ) -> None:
        if self.upsert_started is not None:
            self.upsert_started.set()
        if self.release_upsert is not None:
            self.release_upsert.wait(timeout=2)
        if self.fail_count:
            self.fail_count -= 1
            raise DomainError("INDEX_FAILED", "index unavailable", 503, True)
        self.upserts.append(
            {
                "repository_id": repository_id,
                "ids": ids,
                "texts": texts,
                "embeddings": embeddings,
                "metadatas": metadatas,
            }
        )
        self.ids.update(ids)
        if self.on_upsert is not None:
            self.on_upsert()

    def delete(self, repository_id: str, ids: list[str]) -> None:
        del repository_id
        self.deletes.append(list(ids))
        if self.delete_fail_count:
            self.delete_fail_count -= 1
            raise DomainError("INDEX_FAILED", "delete unavailable", 503, True)
        self.ids.difference_update(ids)


class FakeYuqueGateway:
    def __init__(self) -> None:
        self.create_calls = 0
        self.update_calls = 0
        self.documents: dict[str, YuqueDocumentContent] = {}
        self.lose_create_responses = 0
        self.before_create: Callable[[], None] | None = None

    async def create_document(self, request):  # type: ignore[no-untyped-def]
        self.create_calls += 1
        if self.before_create is not None:
            self.before_create()
        document_id = f"remote-document-{self.create_calls}"
        content = YuqueDocumentContent(
            yuque_id=document_id,
            repository_id=request.repository_id,
            title=request.title,
            content=request.content,
            url=f"https://yuque.test/{document_id}",
        )
        self.documents[document_id] = content
        if self.lose_create_responses:
            self.lose_create_responses -= 1
            raise ConnectionError("create response lost")
        return YuqueDocument(**content.model_dump(exclude={"content"}))

    async def update_document(self, request):  # type: ignore[no-untyped-def]
        self.update_calls += 1
        existing = self.documents.get(request.document_id)
        content = YuqueDocumentContent(
            yuque_id=request.document_id,
            repository_id=(
                existing.repository_id if existing is not None else "remote-repository-1"
            ),
            title=request.title,
            content=request.content,
            url=f"https://yuque.test/{request.document_id}",
        )
        self.documents[request.document_id] = content
        return YuqueDocument(**content.model_dump(exclude={"content"}))

    async def list_documents(self, repository_id: str) -> list[YuqueDocument]:
        return [
            YuqueDocument(**document.model_dump(exclude={"content"}))
            for document in self.documents.values()
            if document.repository_id == repository_id
        ]

    async def read_document(self, document_id: str) -> YuqueDocumentContent:
        return self.documents[document_id]


class CancellingParser(DocumentParser):
    def __init__(self, job_store: ImportJobStore, job_id: str) -> None:
        self.job_store = job_store
        self.job_id = job_id

    def parse(self, document: DownloadedDocument):  # type: ignore[no-untyped-def]
        parsed = super().parse(document)
        self.job_store.request_cancel(self.job_id)
        return parsed


def make_service(
    database,
    tmp_path: Path,
    *,
    source: FakeSourceInspector | None = None,
    vector_store: FakeVectorStore | None = None,
    gateway: FakeYuqueGateway | None = None,
) -> tuple[ImportService, FakeSourceInspector, FakeVectorStore, FakeYuqueGateway]:
    settings = AppSettings(session_token="token", data_dir=tmp_path / "data", environment="test")
    source = source or FakeSourceInspector()
    vector_store = vector_store or FakeVectorStore()
    gateway = gateway or FakeYuqueGateway()
    with database.session() as session:
        session.add(
            RepositoryRecord(
                id="repository-1", yuque_id="remote-repository-1", name="Knowledge"
            )
        )
    service = ImportService(
        settings=settings,
        source_inspector=source,
        parser=DocumentParser(),
        chunker=SemanticChunker(),
        embedding_provider=FakeEmbeddingProvider(EmbeddingSettings(dimension=8)),
        vector_store=vector_store,
        yuque_gateway=gateway,
        repository_store=RepositoryStore(database),
        document_store=DocumentStore(database),
        job_store=ImportJobStore(database),
        event_broker=InMemoryEventBroker(),
    )
    return service, source, vector_store, gateway


async def create_job(service: ImportService, source: FakeSourceInspector, decision=None):
    preview = await source.inspect(SourceRef(kind="url", value="https://example.test/imported.md"))
    return await service.create(
        ImportCreateRequest(
            source=SourceRef(kind="url", value="https://example.test/imported.md"),
            repository_id="repository-1",
            fingerprint=preview.fingerprint,
            duplicate_decision=decision,
        )
    )


def create_indexing_job(
    service: ImportService,
    tmp_path: Path,
    *,
    job_id: str,
    markdown: str,
    old_vector_ids: list[str],
) -> ImportJobRecord:
    markdown_path = tmp_path / f"{job_id}.md"
    markdown_path.write_text(markdown, encoding="utf-8")
    document = service.document_store.create(
        DocumentRecord(
            id=f"{job_id}-document",
            repository_id="repository-1",
            title="Indexed",
            source_url="https://example.test/indexed.md",
            markdown_path=str(markdown_path),
            source_type="import",
        )
    )
    if old_vector_ids:
        service.document_store.replace_chunks(
            document.id,
            [
                DocumentChunkRecord(
                    id=identifier,
                    document_id=document.id,
                    repository_id=document.repository_id,
                    chunk_index=index,
                    text=f"old-{index}",
                    token_count=1,
                    vector_id=identifier,
                )
                for index, identifier in enumerate(old_vector_ids)
            ],
        )
    return service.job_store.create(
        ImportJobRecord(
            id=job_id,
            source_kind="url",
            source_value=json.dumps(
                {
                    "version": 1,
                    "value": "https://example.test/indexed.md",
                    "fingerprint": "index-fingerprint",
                    "duplicate_decision": "update",
                }
            ),
            repository_id="repository-1",
            document_id=document.id,
            state=ImportStatus.INDEXING,
            current_stage=ImportStatus.INDEXING.value,
            progress=70,
        )
    )


async def test_create_rejects_changed_fingerprint_before_persisting_job(database, tmp_path) -> None:
    service, _, _, _ = make_service(database, tmp_path)

    with pytest.raises(DomainError) as error:
        await service.create(
            ImportCreateRequest(
                source=SourceRef(kind="url", value="https://example.test/imported.md"),
                repository_id="repository-1",
                fingerprint="0" * 64,
                duplicate_decision=None,
            )
        )

    assert error.value.code == "SOURCE_CHANGED"
    assert service.job_store.list() == []


async def test_concurrent_create_reserves_same_repository_fingerprint_once(
    database, tmp_path
) -> None:
    service, source, _, _ = make_service(database, tmp_path)
    preview = await source.inspect(SourceRef(kind="url", value=source.document.source_url))
    request = ImportCreateRequest(
        source=SourceRef(kind="url", value=source.document.source_url),
        repository_id="repository-1",
        fingerprint=preview.fingerprint,
    )

    results = await asyncio.gather(
        service.create(request), service.create(request), return_exceptions=True
    )

    assert sum(not isinstance(result, BaseException) for result in results) == 1
    errors = [result for result in results if isinstance(result, DomainError)]
    assert [error.code for error in errors] == ["IMPORT_ALREADY_RUNNING"]
    assert len(service.job_store.list()) == 1


async def test_create_requires_duplicate_decision_and_persists_selected_action(
    database, tmp_path
) -> None:
    service, source, _, _ = make_service(database, tmp_path)
    preview = await source.inspect(SourceRef(kind="url", value="https://example.test/imported.md"))
    service.document_store.create(
        DocumentRecord(
            id="existing-document",
            repository_id="repository-1",
            title="Existing",
            source_url="https://example.test/old.md",
            content_hash=preview.fingerprint,
            yuque_id="remote-existing",
        )
    )

    with pytest.raises(DomainError) as error:
        await create_job(service, source)
    assert error.value.code == "DUPLICATE_DECISION_REQUIRED"

    job = await create_job(service, source, "update")
    await service.run(job.id)

    persisted = service.job_store.get(job.id)
    assert persisted.document_id == "existing-document"  # type: ignore[union-attr]
    assert service.yuque_gateway.update_calls == 1
    assert service.yuque_gateway.create_calls == 0


async def test_run_completes_full_import_with_exact_progress(database, tmp_path) -> None:
    service, source, vector_store, gateway = make_service(database, tmp_path)
    job = await create_job(service, source)

    await service.run(job.id)
    events = [event async for event in service.event_broker.subscribe(job.id, 0)]
    completed = service.job_store.get(job.id)

    assert completed.state == ImportStatus.COMPLETED  # type: ignore[union-attr]
    assert [event.payload["progress"] for event in events] == [0, 20, 45, 70, 90, 100]
    assert events[-1].type == "done"
    assert gateway.create_calls == 1
    assert len(vector_store.upserts) == 1
    document = service.document_store.get(completed.document_id)  # type: ignore[union-attr]
    assert document.yuque_id == "remote-document-1"  # type: ignore[union-attr]
    assert Path(document.raw_path).read_bytes() == source.document.raw_bytes  # type: ignore[arg-type,union-attr]
    assert Path(document.markdown_path).read_text() == "# Imported\n\nUseful text"  # type: ignore[arg-type,union-attr]


async def test_retry_reconciles_remote_marker_after_lost_create_response(
    database, tmp_path
) -> None:
    gateway = FakeYuqueGateway()
    gateway.lose_create_responses = 1
    service, source, _, _ = make_service(database, tmp_path, gateway=gateway)
    job = await create_job(service, source)
    observed_metadata: list[dict[str, Any]] = []
    gateway.before_create = lambda: observed_metadata.append(
        json.loads(service.job_store.get(job.id).source_value)  # type: ignore[union-attr]
    )

    await service.run(job.id)
    failed = service.job_store.get(job.id)
    await service.retry(job.id)
    await service.run(job.id)

    completed = service.job_store.get(job.id)
    document = service.document_store.get(completed.document_id)  # type: ignore[union-attr]
    assert failed.state == ImportStatus.FAILED  # type: ignore[union-attr]
    assert completed.state == ImportStatus.COMPLETED  # type: ignore[union-attr]
    assert gateway.create_calls == 1
    assert len(gateway.documents) == 1
    marker = observed_metadata[0]["upload_intent"]["marker"]
    assert marker == f"docmind-import:{job.id}"
    assert f"<!-- {marker} -->" in next(iter(gateway.documents.values())).content
    assert "docmind-import:" not in Path(document.markdown_path).read_text()  # type: ignore[arg-type,union-attr]


async def test_retry_reconciles_after_remote_create_before_local_update(
    database, tmp_path, monkeypatch
) -> None:
    service, source, _, gateway = make_service(database, tmp_path)
    job = await create_job(service, source)
    original = service.document_store.update_remote
    failures = 1

    def fail_once(document_id: str, *, yuque_id: str, yuque_url: str | None):
        nonlocal failures
        if failures:
            failures -= 1
            raise OSError("local database unavailable")
        return original(document_id, yuque_id=yuque_id, yuque_url=yuque_url)

    monkeypatch.setattr(service.document_store, "update_remote", fail_once)

    await service.run(job.id)
    assert service.job_store.get(job.id).error_code == "UPLOAD_FAILED"  # type: ignore[union-attr]
    await service.retry(job.id)
    await service.run(job.id)

    assert service.job_store.get(job.id).state == ImportStatus.COMPLETED  # type: ignore[union-attr]
    assert gateway.create_calls == 1
    assert len(gateway.documents) == 1


async def test_retry_after_local_attach_failure_does_not_create_remote_again(
    database, tmp_path, monkeypatch
) -> None:
    service, source, _, gateway = make_service(database, tmp_path)
    job = await create_job(service, source)
    original = service.job_store.attach_document
    failures = 1

    def fail_once(job_id: str, document_id: str):
        nonlocal failures
        if failures:
            failures -= 1
            raise OSError("job store unavailable")
        return original(job_id, document_id)

    monkeypatch.setattr(service.job_store, "attach_document", fail_once)

    await service.run(job.id)
    assert service.job_store.get(job.id).error_code == "UPLOAD_FAILED"  # type: ignore[union-attr]
    await service.retry(job.id)
    await service.run(job.id)

    assert service.job_store.get(job.id).state == ImportStatus.COMPLETED  # type: ignore[union-attr]
    assert gateway.create_calls == 1


async def test_filesystem_persistence_error_has_one_stable_terminal(
    database, tmp_path, monkeypatch
) -> None:
    service, source, _, _ = make_service(database, tmp_path)
    job = await create_job(service, source)

    def fail_persist(*args):  # type: ignore[no-untyped-def]
        del args
        raise OSError("disk full")

    monkeypatch.setattr(service, "_persist_parsed_document", fail_persist)

    await service.run(job.id)
    events = [event async for event in service.event_broker.subscribe(job.id, 0)]
    failed = service.job_store.get(job.id)

    assert (failed.state, failed.error_code) == (ImportStatus.FAILED, "PARSE_FAILED")  # type: ignore[union-attr]
    assert [event.type for event in events].count("error") == 1


async def test_cancel_after_filesystem_persistence_stops_before_upload_transition(
    database, tmp_path, monkeypatch
) -> None:
    service, source, _, gateway = make_service(database, tmp_path)
    job = await create_job(service, source)
    original = service._persist_parsed_document

    def persist_then_cancel(*args):  # type: ignore[no-untyped-def]
        original(*args)
        service.job_store.request_cancel(job.id)

    monkeypatch.setattr(service, "_persist_parsed_document", persist_then_cancel)

    await service.run(job.id)
    cancelled = service.job_store.get(job.id)

    assert (cancelled.state, cancelled.progress) == (ImportStatus.CANCELLED, 20)  # type: ignore[union-attr]
    assert gateway.create_calls == 0


async def test_cancel_after_remote_attach_stops_before_indexing_transition(
    database, tmp_path, monkeypatch
) -> None:
    service, source, _, gateway = make_service(database, tmp_path)
    job = await create_job(service, source)
    original = service.job_store.attach_document

    def attach_then_cancel(job_id: str, document_id: str):
        attached = original(job_id, document_id)
        service.job_store.request_cancel(job_id)
        return attached

    monkeypatch.setattr(service.job_store, "attach_document", attach_then_cancel)

    await service.run(job.id)
    cancelled = service.job_store.get(job.id)

    assert (cancelled.state, cancelled.progress) == (ImportStatus.CANCELLED, 45)  # type: ignore[union-attr]
    assert gateway.create_calls == 1


async def test_cancel_requested_after_parsing_prevents_next_external_write(database, tmp_path) -> None:
    service, source, vector_store, gateway = make_service(database, tmp_path)
    job = await create_job(service, source)
    service.parser = CancellingParser(service.job_store, job.id)

    await service.run(job.id)

    cancelled = service.job_store.get(job.id)
    assert cancelled.state == ImportStatus.CANCELLED  # type: ignore[union-attr]
    assert gateway.create_calls == 0
    assert vector_store.upserts == []
    assert list(service.settings.documents_dir.iterdir()) == []


async def test_retry_resumes_indexing_without_creating_second_remote_document(
    database, tmp_path
) -> None:
    service, source, vector_store, gateway = make_service(
        database, tmp_path, vector_store=FakeVectorStore(fail_count=1)
    )
    job = await create_job(service, source)
    await service.run(job.id)

    failed = service.job_store.get(job.id)
    document = service.document_store.get(failed.document_id)  # type: ignore[union-attr]
    assert (failed.state, failed.current_stage, failed.error_code) == (  # type: ignore[union-attr]
        ImportStatus.FAILED,
        ImportStatus.INDEXING.value,
        "INDEX_FAILED",
    )
    assert document.yuque_id == "remote-document-1"  # type: ignore[union-attr]

    retried = await service.retry(job.id)
    await service.run(retried.id)

    assert service.job_store.get(job.id).state == ImportStatus.COMPLETED  # type: ignore[union-attr]
    assert gateway.create_calls == 1
    assert len(vector_store.upserts) == 1


async def test_cancel_after_skip_progress_does_not_complete(database, tmp_path, monkeypatch) -> None:
    service, source, vector_store, _ = make_service(database, tmp_path)
    preview = await source.inspect(SourceRef(kind="url", value=source.document.source_url))
    service.document_store.create(
        DocumentRecord(
            id="skip-document",
            repository_id="repository-1",
            title="Existing",
            content_hash=preview.fingerprint,
            yuque_id="remote-existing",
        )
    )
    job = await create_job(service, source, "skip")
    original = service.job_store.update_progress

    def progress_then_cancel(job_id: str, **kwargs):  # type: ignore[no-untyped-def]
        progressed = original(job_id, **kwargs)
        service.job_store.request_cancel(job_id)
        return progressed

    monkeypatch.setattr(service.job_store, "update_progress", progress_then_cancel)

    await service.run(job.id)

    assert service.job_store.get(job.id).state == ImportStatus.CANCELLED  # type: ignore[union-attr]
    assert vector_store.upserts == []


async def test_cancel_after_vector_upsert_compensates_new_vectors(database, tmp_path) -> None:
    vector_store = FakeVectorStore()
    service, _, _, _ = make_service(database, tmp_path, vector_store=vector_store)
    job = create_indexing_job(
        service, tmp_path, job_id="cancel-vector", markdown="# New\n\ncontent", old_vector_ids=[]
    )
    vector_store.on_upsert = lambda: service.job_store.request_cancel(job.id)

    await service.run(job.id)

    assert service.job_store.get(job.id).state == ImportStatus.CANCELLED  # type: ignore[union-attr]
    assert vector_store.ids == set()
    assert service.document_store.vector_ids(job.document_id or "") == []


async def test_cancel_after_sqlite_replacement_does_not_complete(
    database, tmp_path, monkeypatch
) -> None:
    vector_store = FakeVectorStore()
    service, _, _, _ = make_service(database, tmp_path, vector_store=vector_store)
    job = create_indexing_job(
        service,
        tmp_path,
        job_id="cancel-after-sqlite",
        markdown="# Replacement\n\ncontent",
        old_vector_ids=["old-vector"],
    )
    vector_store.ids.add("old-vector")
    original = service.document_store.replace_chunks

    def replace_then_cancel(document_id: str, chunks: list[DocumentChunkRecord]) -> None:
        original(document_id, chunks)
        service.job_store.request_cancel(job.id)

    monkeypatch.setattr(service.document_store, "replace_chunks", replace_then_cancel)

    await service.run(job.id)

    persisted_ids = service.document_store.vector_ids(job.document_id or "")
    assert service.job_store.get(job.id).state == ImportStatus.CANCELLED  # type: ignore[union-attr]
    assert vector_store.ids == set(persisted_ids)


async def test_sqlite_replacement_failure_compensates_new_vectors(
    database, tmp_path, monkeypatch
) -> None:
    vector_store = FakeVectorStore()
    service, _, _, _ = make_service(database, tmp_path, vector_store=vector_store)
    job = create_indexing_job(
        service,
        tmp_path,
        job_id="sqlite-failure",
        markdown="# Replacement\n\ncontent",
        old_vector_ids=["old-vector"],
    )
    vector_store.ids.add("old-vector")

    def fail_replace(*args):  # type: ignore[no-untyped-def]
        del args
        raise OSError("sqlite unavailable")

    monkeypatch.setattr(service.document_store, "replace_chunks", fail_replace)

    await service.run(job.id)

    failed = service.job_store.get(job.id)
    assert (failed.state, failed.current_stage) == (  # type: ignore[union-attr]
        ImportStatus.FAILED,
        ImportStatus.INDEXING.value,
    )
    assert vector_store.ids == set()
    assert service.document_store.vector_ids(job.document_id or "") == ["old-vector"]


async def test_stale_vector_delete_failure_keeps_sqlite_and_removes_new_vectors(
    database, tmp_path
) -> None:
    vector_store = FakeVectorStore(delete_fail_count=1)
    service, _, _, _ = make_service(database, tmp_path, vector_store=vector_store)
    job = create_indexing_job(
        service,
        tmp_path,
        job_id="delete-failure",
        markdown="# Replacement\n\ncontent",
        old_vector_ids=["old-vector"],
    )
    vector_store.ids.add("old-vector")

    await service.run(job.id)

    assert service.job_store.get(job.id).state == ImportStatus.FAILED  # type: ignore[union-attr]
    assert service.document_store.vector_ids(job.document_id or "") == ["old-vector"]
    assert vector_store.ids == {"old-vector"}


async def test_reindex_with_fewer_chunks_deletes_stale_tail_vectors(database, tmp_path) -> None:
    vector_store = FakeVectorStore()
    service, _, _, _ = make_service(database, tmp_path, vector_store=vector_store)
    old_ids = ["old-zero", "old-one", "old-two"]
    job = create_indexing_job(
        service,
        tmp_path,
        job_id="fewer-chunks",
        markdown="# One\n\nshort replacement",
        old_vector_ids=old_ids,
    )
    vector_store.ids.update(old_ids)

    await service.run(job.id)

    persisted_ids = service.document_store.vector_ids(job.document_id or "")
    assert service.job_store.get(job.id).state == ImportStatus.COMPLETED  # type: ignore[union-attr]
    assert len(persisted_ids) == 1
    assert vector_store.ids == set(persisted_ids)
    assert not set(old_ids) & vector_store.ids


async def test_task_cancellation_waits_for_vector_thread_and_leaves_indexing_state(
    database, tmp_path
) -> None:
    vector_store = FakeVectorStore()
    vector_store.upsert_started = threading.Event()
    vector_store.release_upsert = threading.Event()
    service, _, _, _ = make_service(database, tmp_path, vector_store=vector_store)
    job = create_indexing_job(
        service, tmp_path, job_id="thread-cancel", markdown="# New\n\ncontent", old_vector_ids=[]
    )
    task = asyncio.create_task(service.run(job.id))
    for _ in range(100):
        if vector_store.upsert_started.is_set():
            break
        await asyncio.sleep(0.001)
    assert vector_store.upsert_started.is_set()

    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    vector_store.release_upsert.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert service.job_store.get(job.id).state == ImportStatus.INDEXING  # type: ignore[union-attr]


async def test_task_cancellation_with_cancel_request_persists_cancelled(database, tmp_path) -> None:
    vector_store = FakeVectorStore()
    vector_store.upsert_started = threading.Event()
    vector_store.release_upsert = threading.Event()
    service, _, _, _ = make_service(database, tmp_path, vector_store=vector_store)
    job = create_indexing_job(
        service,
        tmp_path,
        job_id="thread-requested-cancel",
        markdown="# New\n\ncontent",
        old_vector_ids=[],
    )
    task = asyncio.create_task(service.run(job.id))
    for _ in range(100):
        if vector_store.upsert_started.is_set():
            break
        await asyncio.sleep(0.001)
    assert vector_store.upsert_started.is_set()

    service.job_store.request_cancel(job.id)
    task.cancel()
    vector_store.release_upsert.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert service.job_store.get(job.id).state == ImportStatus.CANCELLED  # type: ignore[union-attr]
    assert vector_store.ids == set()


async def test_legacy_plain_source_retry_refreshes_metadata_before_parsing(
    database, tmp_path
) -> None:
    service, source, _, _ = make_service(database, tmp_path)
    job = service.job_store.create(
        ImportJobRecord(
            id="legacy-retry",
            source_kind="url",
            source_value=source.document.source_url,
            repository_id="repository-1",
            state=ImportStatus.FAILED,
            current_stage=ImportStatus.PARSING.value,
            retryable=True,
        )
    )

    retried = await service.retry(job.id)
    await service.run(retried.id)

    persisted = service.job_store.get(job.id)
    metadata = json.loads(persisted.source_value)  # type: ignore[union-attr]
    assert persisted.state == ImportStatus.COMPLETED  # type: ignore[union-attr]
    assert metadata["fingerprint"] == SourcePreview.from_document("url", source.document).fingerprint
    assert metadata["version"] == 2


async def test_uploading_job_with_attached_document_never_creates_remote_again(
    database, tmp_path
) -> None:
    service, source, _, gateway = make_service(database, tmp_path)
    job = await create_job(service, source)
    markdown_path = tmp_path / "persisted.md"
    markdown_path.write_text("# Persisted", encoding="utf-8")
    service.document_store.create(
        DocumentRecord(
            id="attached-document",
            repository_id="repository-1",
            title="Persisted",
            source_url="https://example.test/imported.md",
            markdown_path=str(markdown_path),
            content_hash=(
                await source.inspect(
                    SourceRef(kind="url", value="https://example.test/imported.md")
                )
            ).fingerprint,
        )
    )
    service.job_store.transition(
        job.id,
        expected={ImportStatus.PENDING},
        target=ImportStatus.PARSING,
        progress=20,
        message="parsed",
    )
    service.job_store.attach_document(job.id, "attached-document")
    service.job_store.transition(
        job.id,
        expected={ImportStatus.PARSING},
        target=ImportStatus.UPLOADING,
        progress=45,
        message="uploading",
    )

    await service.run(job.id)

    failed = service.job_store.get(job.id)
    assert failed.state == ImportStatus.FAILED  # type: ignore[union-attr]
    assert failed.error_code == "UPLOAD_FAILED"  # type: ignore[union-attr]
    assert gateway.create_calls == 0


async def test_upload_storage_error_becomes_stable_failure(database, tmp_path) -> None:
    service, source, _, _ = make_service(database, tmp_path)
    job = await create_job(service, source)
    missing_path = tmp_path / "missing.md"
    service.document_store.create(
        DocumentRecord(
            id="remote-document",
            repository_id="repository-1",
            title="Missing",
            source_url="https://example.test/imported.md",
            markdown_path=str(missing_path),
            content_hash=(
                await source.inspect(
                    SourceRef(kind="url", value="https://example.test/imported.md")
                )
            ).fingerprint,
            yuque_id="remote-existing",
        )
    )
    service.job_store.transition(
        job.id,
        expected={ImportStatus.PENDING},
        target=ImportStatus.PARSING,
        progress=20,
        message="parsed",
    )
    service.job_store.attach_document(job.id, "remote-document")
    service.job_store.transition(
        job.id,
        expected={ImportStatus.PARSING},
        target=ImportStatus.UPLOADING,
        progress=45,
        message="uploading",
    )

    await service.run(job.id)

    failed = service.job_store.get(job.id)
    assert failed.state == ImportStatus.FAILED  # type: ignore[union-attr]
    assert failed.error_code == "UPLOAD_FAILED"  # type: ignore[union-attr]


async def test_concurrent_second_runner_returns_import_already_running(database, tmp_path) -> None:
    service, source, _, _ = make_service(database, tmp_path)
    source.load_started = asyncio.Event()
    source.release_load = asyncio.Event()
    job = await create_job(service, source)
    first = asyncio.create_task(service.run(job.id))
    await source.load_started.wait()

    with pytest.raises(DomainError) as error:
        await service.run(job.id)
    assert error.value.code == "IMPORT_ALREADY_RUNNING"

    source.release_load.set()
    await first
