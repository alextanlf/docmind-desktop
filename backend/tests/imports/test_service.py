from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.api.errors import DomainError
from app.config import AppSettings, EmbeddingSettings
from app.core.embedding import FakeEmbeddingProvider
from app.document.chunker import SemanticChunker
from app.document.parser import DocumentParser
from app.document.sources import SourceInspector
from app.imports.batch_service import BatchService
from app.imports.events import InMemoryEventBroker
from app.imports.service import ImportService
from app.schemas.batches import ConfirmBatchInput, ConfirmBatchItem
from app.schemas.imports import (
    DownloadedDocument,
    ImportCreateRequest,
    SourcePreview,
    SourceRef,
)
from app.schemas.yuque import YuqueDocument, YuqueDocumentContent
from app.storage.models import (
    BatchImportRecord,
    BatchItemRecord,
    BatchItemState,
    BatchState,
    DocumentChunkRecord,
    DocumentRecord,
    ImportJobRecord,
    ImportStatus,
    RepositoryRecord,
)
from app.storage.repositories import (
    BatchImportStore,
    DocumentStore,
    ImportJobStore,
    RepositoryStore,
)


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
        self.inspect_started: asyncio.Event | None = None
        self.release_inspect: asyncio.Event | None = None
        self.inspected_refs: list[SourceRef] = []

    async def inspect(self, ref: SourceRef) -> SourcePreview:
        self.inspected_refs.append(ref)
        if self.inspect_started is not None:
            self.inspect_started.set()
        if self.release_inspect is not None:
            await self.release_inspect.wait()
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
        self.delete_started: threading.Event | None = None
        self.release_delete: threading.Event | None = None
        self.on_delete: Callable[[], None] | None = None

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
        if self.delete_started is not None:
            self.delete_started.set()
        if self.release_delete is not None:
            self.release_delete.wait(timeout=2)
        if self.delete_fail_count:
            self.delete_fail_count -= 1
            raise DomainError("INDEX_FAILED", "delete unavailable", 503, True)
        self.ids.difference_update(ids)
        if self.on_delete is not None:
            self.on_delete()


class FakeYuqueGateway:
    def __init__(self) -> None:
        self.create_calls = 0
        self.update_calls = 0
        self.documents: dict[str, YuqueDocumentContent] = {}
        self.lose_create_responses = 0
        self.before_create: Callable[[], None] | None = None
        self.after_list: Callable[[], None] | None = None
        self.after_read: Callable[[], None] | None = None
        self.after_update: Callable[[], None] | None = None

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
        if self.after_update is not None:
            self.after_update()
        return YuqueDocument(**content.model_dump(exclude={"content"}))

    async def list_documents(self, repository_id: str) -> list[YuqueDocument]:
        documents = [
            YuqueDocument(**document.model_dump(exclude={"content"}))
            for document in self.documents.values()
            if document.repository_id == repository_id
        ]
        if self.after_list is not None:
            self.after_list()
        return documents

    async def read_document(self, document_id: str) -> YuqueDocumentContent:
        document = self.documents[document_id]
        if self.after_read is not None:
            self.after_read()
        return document


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
    seed_repository: bool = True,
) -> tuple[ImportService, FakeSourceInspector, FakeVectorStore, FakeYuqueGateway]:
    settings = AppSettings(session_token="token", data_dir=tmp_path / "data", environment="test")
    source = source or FakeSourceInspector()
    vector_store = vector_store or FakeVectorStore()
    gateway = gateway or FakeYuqueGateway()
    if seed_repository:
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


def create_collection_batch(
    service: ImportService,
    database,
    raw_bytes: bytes = b"# Nested guide\n\nUseful text",
) -> tuple[BatchImportStore, BatchImportRecord, BatchItemRecord, Path]:
    collection_id = str(uuid4())
    cache_id = str(uuid4())
    digest = hashlib.sha256(raw_bytes).hexdigest()
    collection_root = service.settings.staging_dir / "collections" / collection_id
    item_path = collection_root / "items" / cache_id
    item_path.parent.mkdir(parents=True)
    item_path.write_bytes(raw_bytes)
    (collection_root / "manifest.json").write_text(
        json.dumps(
            {
                "rootId": "root-1",
                "files": [
                    {
                        "relativePath": "docs/guide.md",
                        "stagedId": cache_id,
                        "mediaType": "text/markdown",
                        "sizeBytes": len(raw_bytes),
                        "sha256": digest,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    store = BatchImportStore(database)
    batch = store.create_batch(
        BatchImportRecord(
            id=str(uuid4()),
            source_kind="staged_directory",
            source_descriptor_json=json.dumps({"collectionId": collection_id}),
            repository_id="repository-1",
            state=BatchState.AWAITING_CONFIRMATION,
            discovery_version=1,
            message="ready",
        )
    )
    item = BatchItemRecord(
        id=str(uuid4()),
        source_identity="folder:root-1:docs/guide.md",
        source_revision=digest,
        title="Nested guide",
        display_path="docs/guide.md",
        media_type="text/markdown",
        size_bytes=len(raw_bytes),
        cached_source_json=json.dumps(
            {
                "cache_id": cache_id,
                "media_type": "text/markdown",
                "byte_size": len(raw_bytes),
                "sha256": digest,
            }
        ),
        allowed_actions_json=json.dumps(["create", "skip"]),
    )
    store.insert_discovered_items(batch.id, [item])
    return store, batch, item, item_path


@pytest.mark.asyncio
async def test_batch_collection_child_parses_nested_cache_and_persists_source_identity(
    database, tmp_path: Path
) -> None:
    service, _, _, _ = make_service(database, tmp_path)
    service.source_inspector = SourceInspector(service.settings)
    store, batch, item, _ = create_collection_batch(service, database)
    coordinator = BatchService(
        store=store,
        import_service=service,
        document_store=service.document_store,
    )
    confirmation = ConfirmBatchInput(
        discovery_version=1,
        items=[ConfirmBatchItem(item_id=item.id, decision="create")],
    )

    await coordinator.confirm(batch.id, confirmation)
    reserved_item = store.get_item(item.id)
    assert reserved_item is not None
    job_id = reserved_item.import_job_id or ""
    service.job_store.transition(
        job_id,
        expected={ImportStatus.PENDING},
        target=ImportStatus.PARSING,
        progress=0,
        message="parsing",
    )
    service.job_store.fail(
        job_id,
        code="TRANSIENT",
        message="retry",
        retryable=True,
    )
    await service.retry(job_id)
    retry_metadata = json.loads(service.job_store.get(job_id).source_value)  # type: ignore[union-attr]
    assert retry_metadata["source_identity"] == item.source_identity
    assert retry_metadata["source_revision"] == item.source_revision
    await coordinator.continue_batch(batch.id)

    persisted_item = store.get_item(item.id)
    assert persisted_item is not None
    assert persisted_item.state is BatchItemState.COMPLETED
    job = service.job_store.get(persisted_item.import_job_id or "")
    assert job is not None
    metadata = json.loads(job.source_value)
    assert metadata["source_identity"] == item.source_identity
    assert metadata["source_revision"] == item.source_revision
    document = service.document_store.get(job.document_id or "")
    assert document is not None
    assert (document.source_identity, document.source_revision) == (
        item.source_identity,
        item.source_revision,
    )


@pytest.mark.asyncio
async def test_attach_remote_binds_local_snapshot_without_gateway_writes(database, tmp_path: Path) -> None:
    service, _, _, gateway = make_service(database, tmp_path)
    service.source_inspector = SourceInspector(service.settings)
    store, batch, item, _ = create_collection_batch(service, database)
    with database.session() as session:
        persisted = session.get(BatchItemRecord, item.id)
        assert persisted is not None
        persisted.allowed_actions_json = json.dumps(["attach_remote", "skip"])
        persisted.remote_binding_json = json.dumps({
            "repository_id": "remote-repository-1",
            "document_id": "remote-existing",
            "document_url": "https://yuque.test/remote-existing",
        })
    coordinator = BatchService(store=store, import_service=service, document_store=service.document_store)
    await coordinator.confirm(batch.id, ConfirmBatchInput(
        discovery_version=1,
        items=[ConfirmBatchItem(item_id=item.id, decision="attach_remote")],
    ))
    reserved_item = store.get_item(item.id)
    assert reserved_item is not None
    job_id = reserved_item.import_job_id or ""
    service.job_store.transition(
        job_id,
        expected={ImportStatus.PENDING},
        target=ImportStatus.PARSING,
        progress=0,
        message="parsing",
    )
    assert service.job_store.recover_interrupted() == 1
    recovered_metadata = json.loads(service.job_store.get(job_id).source_value)  # type: ignore[union-attr]
    assert recovered_metadata["attach_remote"] is True
    assert recovered_metadata["remote_binding"]["document_id"] == "remote-existing"
    await service.retry(job_id)
    retried_metadata = json.loads(service.job_store.get(job_id).source_value)  # type: ignore[union-attr]
    assert retried_metadata["attach_remote"] is True
    assert retried_metadata["remote_binding"]["document_id"] == "remote-existing"
    await coordinator.continue_batch(batch.id)
    persisted_item = store.get_item(item.id)
    assert persisted_item is not None
    job = service.job_store.get(persisted_item.import_job_id or "")
    assert job is not None
    metadata = json.loads(job.source_value)
    assert metadata["attach_remote"] is True
    assert metadata["remote_binding"]["document_id"] == "remote-existing"
    document = service.document_store.get(job.document_id or "")
    assert document is not None
    assert document.yuque_id == "remote-existing"
    assert document.yuque_url == "https://yuque.test/remote-existing"
    assert gateway.create_calls == gateway.update_calls == 0


@pytest.mark.asyncio
async def test_tampered_collection_cache_aborts_confirmation_without_jobs(
    database, tmp_path: Path
) -> None:
    service, _, _, _ = make_service(database, tmp_path)
    service.source_inspector = SourceInspector(service.settings)
    store, batch, item, item_path = create_collection_batch(service, database)
    item_path.write_bytes(b"# Tampered\n")
    coordinator = BatchService(
        store=store,
        import_service=service,
        document_store=service.document_store,
    )

    with pytest.raises(DomainError) as error:
        await coordinator.confirm(
            batch.id,
            ConfirmBatchInput(
                discovery_version=1,
                items=[ConfirmBatchItem(item_id=item.id, decision="create")],
            ),
        )

    assert error.value.code == "BATCH_STALE_CONFIRMATION"
    assert service.job_store.list() == []
    assert store.get(batch.id).state is BatchState.AWAITING_CONFIRMATION  # type: ignore[union-attr]
    persisted_item = store.get_item(item.id)
    assert persisted_item is not None
    assert (persisted_item.state, persisted_item.decision, persisted_item.selected) == (
        BatchItemState.DISCOVERED,
        None,
        False,
    )


@pytest.mark.asyncio
async def test_malformed_collection_cache_metadata_is_stable_confirmation_error(
    database, tmp_path: Path
) -> None:
    service, _, _, _ = make_service(database, tmp_path)
    service.source_inspector = SourceInspector(service.settings)
    store, batch, item, _ = create_collection_batch(service, database)
    with database.session() as session:
        persisted = session.get(BatchItemRecord, item.id)
        assert persisted is not None
        persisted.cached_source_json = "not-json"

    coordinator = BatchService(
        store=store,
        import_service=service,
        document_store=service.document_store,
    )
    with pytest.raises(DomainError) as error:
        await coordinator.confirm(
            batch.id,
            ConfirmBatchInput(
                discovery_version=1,
                items=[ConfirmBatchItem(item_id=item.id, decision="create")],
            ),
        )

    assert error.value.code == "BATCH_STALE_CONFIRMATION"
    assert service.job_store.list() == []


@pytest.mark.asyncio
async def test_collection_cache_is_revalidated_immediately_before_parsing(
    database, tmp_path: Path
) -> None:
    service, _, _, _ = make_service(database, tmp_path)
    service.source_inspector = SourceInspector(service.settings)
    store, batch, item, item_path = create_collection_batch(service, database)
    coordinator = BatchService(
        store=store,
        import_service=service,
        document_store=service.document_store,
    )
    await coordinator.confirm(
        batch.id,
        ConfirmBatchInput(
            discovery_version=1,
            items=[ConfirmBatchItem(item_id=item.id, decision="create")],
        ),
    )
    item_path.write_bytes(b"# Changed after confirmation\n")

    await coordinator.continue_batch(batch.id)

    persisted_item = store.get_item(item.id)
    assert persisted_item is not None
    assert (persisted_item.state, persisted_item.error_code) == (
        BatchItemState.FAILED,
        "BATCH_SOURCE_CHANGED",
    )
    job = service.job_store.get(persisted_item.import_job_id or "")
    assert job is not None
    assert (job.state, job.document_id) == (ImportStatus.FAILED, None)
    assert service.document_store.list_for_repository("repository-1") == []


async def create_full_update_job(
    service: ImportService,
    source: FakeSourceInspector,
    vector_store: FakeVectorStore,
    gateway: FakeYuqueGateway,
    tmp_path: Path,
) -> ImportJobRecord:
    preview = await source.inspect(SourceRef(kind="url", value=source.document.source_url))
    document_dir = tmp_path / "existing-document"
    document_dir.mkdir()
    raw_path = document_dir / "raw.bin"
    markdown_path = document_dir / "document.md"
    raw_path.write_bytes(b"# Old\n\nold text")
    markdown_path.write_text("# Old\n\nold text", encoding="utf-8")
    service.document_store.create(
        DocumentRecord(
            id="existing-document",
            repository_id="repository-1",
            title="Old",
            source_url="https://example.test/old.md",
            raw_path=str(raw_path),
            markdown_path=str(markdown_path),
            source_type="import",
            content_hash=preview.fingerprint,
            yuque_id="remote-existing",
            yuque_url="https://yuque.test/remote-existing",
        )
    )
    service.document_store.replace_chunks(
        "existing-document",
        [
            DocumentChunkRecord(
                id="old-chunk",
                document_id="existing-document",
                repository_id="repository-1",
                chunk_index=0,
                text="# Old\n\nold text",
                token_count=4,
                vector_id="old-vector",
            )
        ],
    )
    vector_store.ids.add("old-vector")
    gateway.documents["remote-existing"] = YuqueDocumentContent(
        yuque_id="remote-existing",
        repository_id="remote-repository-1",
        title="Old",
        content="# Old\n\nold text",
        url="https://yuque.test/remote-existing",
    )
    view = await create_job(service, source, "update")
    record = service.job_store.get(view.id)
    assert record is not None
    return record


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
    metadata = json.loads(completed.source_value)  # type: ignore[union-attr]
    assert metadata["last_event_sequence"] == events[-1].sequence == 6
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


@pytest.mark.parametrize(
    "boundary",
    ["local_persist", "remote_update", "vector_upsert", "sqlite_replace"],
)
async def test_update_cancellation_reaches_a_coherent_boundary_before_cancelling(
    database, tmp_path, monkeypatch, boundary: str
) -> None:
    service, source, vector_store, gateway = make_service(database, tmp_path)
    job = await create_full_update_job(service, source, vector_store, gateway, tmp_path)

    if boundary == "local_persist":
        original_persist = service._persist_parsed_document

        def persist_then_cancel(*args):  # type: ignore[no-untyped-def]
            original_persist(*args)
            service.job_store.request_cancel(job.id)

        monkeypatch.setattr(service, "_persist_parsed_document", persist_then_cancel)
    elif boundary == "remote_update":
        gateway.after_update = lambda: service.job_store.request_cancel(job.id)
    elif boundary == "vector_upsert":
        vector_store.on_upsert = lambda: service.job_store.request_cancel(job.id)
    else:
        original_replace = service.document_store.replace_chunks

        def replace_then_cancel(
            document_id: str, chunks: list[DocumentChunkRecord]
        ) -> None:
            original_replace(document_id, chunks)
            service.job_store.request_cancel(job.id)

        monkeypatch.setattr(service.document_store, "replace_chunks", replace_then_cancel)

    await service.run(job.id)

    persisted_job = service.job_store.get(job.id)
    document = service.document_store.get("existing-document")
    vector_ids = service.document_store.vector_ids("existing-document")
    assert persisted_job is not None
    assert document is not None
    assert persisted_job.state == ImportStatus.CANCELLED
    assert Path(document.raw_path or "").read_bytes() == b"# Imported\n\nUseful text"
    assert Path(document.markdown_path or "").read_text(encoding="utf-8") == (
        "# Imported\n\nUseful text"
    )
    assert gateway.documents["remote-existing"].content == (
        f"# Imported\n\nUseful text\n\n<!-- docmind-import:{job.id} -->\n"
    )
    assert [
        chunk.text for chunk in service.document_store.list_chunks("existing-document")
    ] == ["# Imported\n\nUseful text"]
    assert vector_ids
    assert vector_store.ids == set(vector_ids)
    assert "old-vector" not in vector_store.ids


async def test_restart_keeps_cancelled_update_retryable_until_coherence_is_restored(
    database, tmp_path, monkeypatch
) -> None:
    service, source, vector_store, gateway = make_service(database, tmp_path)
    job = await create_full_update_job(service, source, vector_store, gateway, tmp_path)
    original_persist = service._persist_parsed_document

    def persist_then_interrupt(*args):  # type: ignore[no-untyped-def]
        original_persist(*args)
        service.job_store.request_cancel(job.id)
        raise asyncio.CancelledError

    monkeypatch.setattr(service, "_persist_parsed_document", persist_then_interrupt)

    with pytest.raises(asyncio.CancelledError):
        await service.run(job.id)

    interrupted = service.job_store.get(job.id)
    assert interrupted is not None
    assert json.loads(interrupted.source_value)["coherence_pending"] is True
    assert service.job_store.recover_interrupted() == 1
    recovered = service.job_store.get(job.id)
    assert recovered is not None
    assert (recovered.state, recovered.error_code, recovered.retryable) == (
        ImportStatus.FAILED,
        "APP_RESTARTED",
        True,
    )

    monkeypatch.setattr(service, "_persist_parsed_document", original_persist)
    await service.retry(job.id)
    await service.run(job.id)

    completed = service.job_store.get(job.id)
    vector_ids = service.document_store.vector_ids("existing-document")
    assert completed is not None
    assert completed.state == ImportStatus.COMPLETED
    assert gateway.documents["remote-existing"].content == (
        f"# Imported\n\nUseful text\n\n<!-- docmind-import:{job.id} -->\n"
    )
    assert vector_store.ids == set(vector_ids)
    assert "old-vector" not in vector_store.ids


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


async def test_cancel_during_remote_list_prevents_create(database, tmp_path) -> None:
    service, source, _, gateway = make_service(database, tmp_path)
    job = await create_job(service, source)
    gateway.after_list = lambda: service.job_store.request_cancel(job.id)

    await service.run(job.id)

    cancelled = service.job_store.get(job.id)
    assert (cancelled.state, cancelled.progress) == (ImportStatus.CANCELLED, 45)  # type: ignore[union-attr]
    assert gateway.create_calls == 0


async def test_cancel_during_remote_read_prevents_create(database, tmp_path) -> None:
    service, source, _, gateway = make_service(database, tmp_path)
    gateway.documents["unrelated"] = YuqueDocumentContent(
        yuque_id="unrelated",
        repository_id="remote-repository-1",
        title="Unrelated",
        content="# No import marker",
        url="https://yuque.test/unrelated",
    )
    job = await create_job(service, source)
    gateway.after_read = lambda: service.job_store.request_cancel(job.id)

    await service.run(job.id)

    cancelled = service.job_store.get(job.id)
    assert (cancelled.state, cancelled.progress) == (ImportStatus.CANCELLED, 45)  # type: ignore[union-attr]
    assert gateway.create_calls == 0


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


async def test_cancelled_created_vector_cleanup_is_recovered_by_new_service(
    database, tmp_path
) -> None:
    vector_store = FakeVectorStore(delete_fail_count=1)
    service, source, _, gateway = make_service(
        database, tmp_path, vector_store=vector_store
    )
    job = create_indexing_job(
        service,
        tmp_path,
        job_id="cancel-created-cleanup",
        markdown="# New\n\ncontent",
        old_vector_ids=[],
    )
    vector_store.on_upsert = lambda: service.job_store.request_cancel(job.id)

    await service.run(job.id)

    cancelled = service.job_store.get(job.id)
    metadata = json.loads(cancelled.source_value)  # type: ignore[union-attr]
    pending = metadata["pending_created_vector_ids"]
    assert cancelled.state == ImportStatus.CANCELLED  # type: ignore[union-attr]
    assert set(pending) == vector_store.ids
    assert service.document_store.vector_ids(job.document_id or "") == []

    recovered, _, _, _ = make_service(
        database,
        tmp_path,
        source=source,
        vector_store=vector_store,
        gateway=gateway,
        seed_repository=False,
    )
    assert await recovered.recover_pending_vector_cleanup() == 1
    assert vector_store.ids == set()
    recovered_metadata = json.loads(recovered.job_store.get(job.id).source_value)  # type: ignore[union-attr]
    assert recovered_metadata["pending_created_vector_ids"] == []


async def test_failed_cancelled_cleanup_remains_pending_for_next_recovery(
    database, tmp_path
) -> None:
    vector_store = FakeVectorStore(delete_fail_count=2)
    service, source, _, gateway = make_service(
        database, tmp_path, vector_store=vector_store
    )
    job = create_indexing_job(
        service,
        tmp_path,
        job_id="cancel-cleanup-retry",
        markdown="# New\n\ncontent",
        old_vector_ids=[],
    )
    vector_store.on_upsert = lambda: service.job_store.request_cancel(job.id)
    await service.run(job.id)
    pending = json.loads(service.job_store.get(job.id).source_value)[  # type: ignore[union-attr]
        "pending_created_vector_ids"
    ]

    recovered, _, _, _ = make_service(
        database,
        tmp_path,
        source=source,
        vector_store=vector_store,
        gateway=gateway,
        seed_repository=False,
    )
    assert await recovered.recover_pending_vector_cleanup() == 0
    persisted = json.loads(recovered.job_store.get(job.id).source_value)  # type: ignore[union-attr]
    assert persisted["pending_created_vector_ids"] == pending
    assert set(pending) == vector_store.ids


async def test_failed_overlapping_upsert_does_not_delete_existing_vectors(
    database, tmp_path
) -> None:
    vector_store = FakeVectorStore(fail_count=1)
    service, _, _, _ = make_service(database, tmp_path, vector_store=vector_store)
    job = create_indexing_job(
        service,
        tmp_path,
        job_id="overlap-failure",
        markdown="# Same\n\ncontent",
        old_vector_ids=[],
    )
    document = service.document_store.get(job.document_id or "")
    assert document is not None
    records = service._chunk_records(
        job.id,
        document,
        service.chunker.chunk(service._parsed_from_persisted(document)),
    )
    service.document_store.replace_chunks(document.id, records)
    old_ids = [record.vector_id or record.id for record in records]
    vector_store.ids.update(old_ids)

    await service.run(job.id)

    assert service.job_store.get(job.id).state == ImportStatus.FAILED  # type: ignore[union-attr]
    assert vector_store.ids == set(old_ids)
    assert not any(set(old_ids) & set(deleted) for deleted in vector_store.deletes)


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


async def test_task_cancel_during_stale_delete_finishes_coherent_cancel(
    database, tmp_path
) -> None:
    vector_store = FakeVectorStore()
    vector_store.delete_started = threading.Event()
    vector_store.release_delete = threading.Event()
    service, _, _, _ = make_service(database, tmp_path, vector_store=vector_store)
    job = create_indexing_job(
        service,
        tmp_path,
        job_id="cancel-stale-delete",
        markdown="# Replacement\n\ncontent",
        old_vector_ids=["old-vector"],
    )
    vector_store.ids.add("old-vector")
    task = asyncio.create_task(service.run(job.id))
    for _ in range(100):
        if vector_store.delete_started.is_set():
            break
        await asyncio.sleep(0.001)
    assert vector_store.delete_started.is_set()

    service.job_store.request_cancel(job.id)
    task.cancel()
    vector_store.release_delete.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    persisted_ids = service.document_store.vector_ids(job.document_id or "")
    assert service.job_store.get(job.id).state == ImportStatus.CANCELLED  # type: ignore[union-attr]
    assert set(persisted_ids) <= vector_store.ids


async def test_cancelled_stale_cleanup_is_recovered_without_deleting_current_vectors(
    database, tmp_path
) -> None:
    vector_store = FakeVectorStore(delete_fail_count=2)
    vector_store.delete_started = threading.Event()
    vector_store.release_delete = threading.Event()
    service, source, _, gateway = make_service(
        database, tmp_path, vector_store=vector_store
    )
    job = create_indexing_job(
        service,
        tmp_path,
        job_id="cancel-stale-cleanup",
        markdown="# Replacement\n\ncontent",
        old_vector_ids=["old-vector"],
    )
    vector_store.ids.add("old-vector")
    task = asyncio.create_task(service.run(job.id))
    for _ in range(100):
        if vector_store.delete_started.is_set():
            break
        await asyncio.sleep(0.001)
    assert vector_store.delete_started.is_set()

    service.job_store.request_cancel(job.id)
    task.cancel()
    vector_store.release_delete.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    persisted_ids = service.document_store.vector_ids(job.document_id or "")
    metadata = json.loads(service.job_store.get(job.id).source_value)  # type: ignore[union-attr]
    assert service.job_store.get(job.id).state == ImportStatus.CANCELLED  # type: ignore[union-attr]
    assert metadata["stale_vector_ids"] == ["old-vector"]
    assert set(persisted_ids) <= vector_store.ids

    recovered, _, _, _ = make_service(
        database,
        tmp_path,
        source=source,
        vector_store=vector_store,
        gateway=gateway,
        seed_repository=False,
    )
    assert await recovered.recover_pending_vector_cleanup() == 1
    assert vector_store.ids == set(persisted_ids)
    recovered_metadata = json.loads(recovered.job_store.get(job.id).source_value)  # type: ignore[union-attr]
    assert recovered_metadata["stale_vector_ids"] == []


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
    assert vector_store.ids == {"old-vector"}
    assert service.document_store.vector_ids(job.document_id or "") == ["old-vector"]


async def test_stale_vector_cleanup_is_durable_and_idempotent_after_sqlite_commit(
    database, tmp_path
) -> None:
    vector_store = FakeVectorStore(delete_fail_count=2)
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

    failed = service.job_store.get(job.id)
    first_persisted_ids = service.document_store.vector_ids(job.document_id or "")
    assert failed.state == ImportStatus.FAILED  # type: ignore[union-attr]
    assert set(first_persisted_ids) <= vector_store.ids
    assert json.loads(failed.source_value)["stale_vector_ids"] == ["old-vector"]  # type: ignore[union-attr]

    await service.retry(job.id)
    await service.run(job.id)

    persisted_ids = service.document_store.vector_ids(job.document_id or "")
    assert service.job_store.get(job.id).state == ImportStatus.COMPLETED  # type: ignore[union-attr]
    assert persisted_ids != ["old-vector"]
    assert vector_store.ids == set(persisted_ids)
    assert vector_store.deletes == [["old-vector"], ["old-vector"], ["old-vector"]]


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


async def test_plain_legacy_terminal_then_retry_preserves_source_and_sequence(
    database, tmp_path
) -> None:
    service, source, _, _ = make_service(database, tmp_path)
    source_value = source.document.source_url
    job = service.job_store.create(
        ImportJobRecord(
            id="legacy-terminal-retry",
            source_kind="url",
            source_value=source_value,
            repository_id="repository-1",
            state=ImportStatus.FAILED,
            current_stage=ImportStatus.PARSING.value,
            retryable=True,
        )
    )

    await service.ensure_terminal_event(job.id, service.get(job.id))
    await service.retry(job.id)

    assert source.inspected_refs[-1] == SourceRef(kind="url", value=source_value)
    metadata = json.loads(service.job_store.get(job.id).source_value)  # type: ignore[union-attr]
    assert (metadata["version"], metadata["value"], metadata["last_event_sequence"]) == (
        2,
        source_value,
        1,
    )

    await service._publish_event(job.id, "progress", {"progress": 0})
    next_event = service.event_broker.subscribe(job.id, 1)
    event = await asyncio.wait_for(anext(next_event), timeout=0.1)
    await next_event.aclose()
    assert event.sequence == 2


async def test_retry_merge_preserves_sequence_allocated_while_inspect_waits(
    database, tmp_path
) -> None:
    service, source, _, _ = make_service(database, tmp_path)
    source.inspect_started = asyncio.Event()
    source.release_inspect = asyncio.Event()
    source_value = source.document.source_url
    job = service.job_store.create(
        ImportJobRecord(
            id="legacy-concurrent-sequence",
            source_kind="url",
            source_value=source_value,
            repository_id="repository-1",
            state=ImportStatus.FAILED,
            current_stage=ImportStatus.PARSING.value,
            retryable=True,
        )
    )
    retry = asyncio.create_task(service.retry(job.id))
    await source.inspect_started.wait()

    assert service.job_store.allocate_event_sequence(job.id) == 1
    source.release_inspect.set()
    await retry

    metadata = json.loads(service.job_store.get(job.id).source_value)  # type: ignore[union-attr]
    assert (metadata["version"], metadata["value"], metadata["last_event_sequence"]) == (
        2,
        source_value,
        1,
    )
    await service._publish_event(job.id, "progress", {"progress": 0})
    assert json.loads(service.job_store.get(job.id).source_value)[  # type: ignore[union-attr]
        "last_event_sequence"
    ] == 2


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
