from __future__ import annotations

import asyncio
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
from app.storage.models import DocumentRecord, ImportStatus, RepositoryRecord
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
    def __init__(self, fail_count: int = 0) -> None:
        self.fail_count = fail_count
        self.upserts: list[dict[str, Any]] = []

    def upsert(
        self,
        repository_id: str,
        ids: list[str],
        texts: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
    ) -> None:
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


class FakeYuqueGateway:
    def __init__(self) -> None:
        self.create_calls = 0
        self.update_calls = 0

    async def create_document(self, request):  # type: ignore[no-untyped-def]
        from app.schemas.yuque import YuqueDocument

        self.create_calls += 1
        return YuqueDocument(
            yuque_id="remote-document-1",
            repository_id=request.repository_id,
            title=request.title,
            url="https://yuque.test/remote-document-1",
        )

    async def update_document(self, request):  # type: ignore[no-untyped-def]
        from app.schemas.yuque import YuqueDocument

        self.update_calls += 1
        return YuqueDocument(
            yuque_id=request.document_id,
            repository_id="remote-repository-1",
            title=request.title,
            url=f"https://yuque.test/{request.document_id}",
        )


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
