from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.requests import Request

from app.api.documents import list_documents
from app.config import AppSettings
from app.core.embedding import FakeEmbeddingProvider
from app.core.secrets import MemorySecretStore
from app.main import create_app
from app.schemas.yuque import CreateYuqueDocumentRequest
from app.storage.database import Database
from app.storage.models import DocumentChunkRecord, DocumentRecord
from app.storage.repositories import DocumentMutationStore, RepositoryStore
from app.yuque.gateway import FakeYuqueGateway


class RecordingVectorStore:
    def __init__(self) -> None:
        self.ids: set[str] = set()
        self.fail_upsert = False

    def upsert(self, repository_id, ids, texts, embeddings, metadatas):  # type: ignore[no-untyped-def]
        del repository_id, texts, embeddings, metadatas
        if self.fail_upsert:
            raise RuntimeError("upsert failed")
        self.ids.update(ids)

    def delete(self, repository_id, ids):  # type: ignore[no-untyped-def]
        del repository_id
        self.ids.difference_update(ids)


def _install_gateway(client, gateway: FakeYuqueGateway | None = None):  # type: ignore[no-untyped-def]
    gateway = gateway or FakeYuqueGateway()
    remote = gateway.seed_repository("repo-remote", "SwiftUI")
    repository = client.app.state.repository_store.upsert_remote(
        yuque_id=remote.yuque_id,
        name=remote.name,
        description=None,
        yuque_url=remote.url,
    )
    client.app.state.yuque_gateway = gateway
    client.app.state.embedding_provider = FakeEmbeddingProvider(
        client.app.state.settings.embedding_settings
    )
    return repository, gateway


def _install_vector_store(client) -> RecordingVectorStore:  # type: ignore[no-untyped-def]
    vector_store = RecordingVectorStore()
    client.app.state.vector_store = vector_store
    return vector_store


def _recreated_client(client, gateway: FakeYuqueGateway) -> TestClient:  # type: ignore[no-untyped-def]
    settings = AppSettings(
        session_token=client.app.state.settings.session_token,
        data_dir=client.app.state.settings.data_dir,
        environment="test",
    )
    return TestClient(
        create_app(
            settings,
            secret_store=MemorySecretStore(),
            yuque_gateway=gateway,
            embedding_provider=FakeEmbeddingProvider(settings.embedding_settings),
        )
    )


def _seed_vector_owner(client, repository_id: str, vector_id: str) -> str:  # type: ignore[no-untyped-def]
    owner = client.app.state.document_store.create(
        DocumentRecord(
            id=str(uuid4()),
            repository_id=repository_id,
            title="Vector owner",
            source_type="yuque",
            status="uploaded",
        )
    )
    client.app.state.document_store.replace_chunks(
        owner.id,
        [
            DocumentChunkRecord(
                id=str(uuid4()),
                document_id=owner.id,
                repository_id=repository_id,
                chunk_index=0,
                text="shared vector owner",
                token_count=3,
                vector_id=vector_id,
            )
        ],
    )
    return owner.id


def test_create_compensation_preserves_vector_owned_by_another_document(
    client, auth_headers, monkeypatch
) -> None:
    """Deleting a failed create's vectors must not remove another document's live vector."""
    repository, _ = _install_gateway(client)
    vector_store = _install_vector_store(client)
    shared_id = "00000000-0000-0000-0000-000000000123"
    owner_id = _seed_vector_owner(client, repository.id, shared_id)
    vector_store.ids.add(shared_id)
    monkeypatch.setattr("app.api.documents.uuid5", lambda *_: UUID(shared_id))

    def fail_replace(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise OSError("file replace failed")

    monkeypatch.setattr("app.api.documents.os.replace", fail_replace)
    response = client.post(
        f"/api/repositories/{repository.id}/documents",
        headers=auth_headers,
        json={"title": "Failed create", "content": "# Shared"},
    )

    assert response.status_code == 503
    assert client.app.state.document_store.vector_ids(owner_id) == [shared_id]
    assert shared_id in vector_store.ids


def test_successful_update_preserves_stale_vector_owned_by_another_document(
    client, auth_headers
) -> None:
    """A successful replacement must not delete a stale ID still owned cross-document."""
    repository, _ = _install_gateway(client)
    vector_store = _install_vector_store(client)
    created = client.post(
        f"/api/repositories/{repository.id}/documents",
        headers=auth_headers,
        json={"title": "Original", "content": "# Original\n\nold body"},
    )
    document_id = created.json()["id"]
    shared_id = client.app.state.document_store.vector_ids(document_id)[0]
    owner_id = _seed_vector_owner(client, repository.id, shared_id)

    updated = client.put(
        f"/api/documents/{document_id}",
        headers=auth_headers,
        json={"title": "Changed", "content": "# Changed\n\nnew body"},
    )

    assert updated.status_code == 200
    assert client.app.state.document_store.vector_ids(owner_id) == [shared_id]
    assert shared_id in vector_store.ids


class LostCreateResponseGateway(FakeYuqueGateway):
    def __init__(self) -> None:
        super().__init__()
        self.create_calls = 0
        self.discovery_available = False
        self.intent_payload_at_submit: dict[str, object] = {}
        self.submitted_content = ""
        self.read_intent = dict

    async def create_document(self, request):  # type: ignore[no-untyped-def]
        self.create_calls += 1
        self.submitted_content = request.content
        created = await super().create_document(request)
        self.intent_payload_at_submit = self.read_intent()
        del created
        raise ConnectionError("create acknowledgement lost")

    async def find_document_by_marker(self, repository_id: str, marker: str):  # type: ignore[no-untyped-def]
        if not self.discovery_available:
            raise ConnectionError("discovery unavailable")
        return await super().find_document_by_marker(repository_id, marker)


def test_lost_create_response_is_discovered_and_compensated_after_restart(
    client, auth_headers
) -> None:
    """A create side effect without a returned ID stays discoverable without resubmission."""
    gateway = LostCreateResponseGateway()
    repository, _ = _install_gateway(client, gateway)
    _install_vector_store(client)

    def read_intent() -> dict[str, object]:
        records = client.app.state.document_mutation_store.list()
        assert len(records) == 1
        return json.loads(records[0].payload_json)

    gateway.read_intent = read_intent
    response = client.post(
        f"/api/repositories/{repository.id}/documents",
        headers=auth_headers,
        json={"title": "Crash safe", "content": "# Crash safe\n\nbody"},
    )

    assert response.status_code == 503
    assert gateway.create_calls == 1
    assert gateway.intent_payload_at_submit["requested_title"] == "Crash safe"
    assert gateway.intent_payload_at_submit["requested_content"] == "# Crash safe\n\nbody"
    marker = gateway.intent_payload_at_submit["marker"]
    assert isinstance(marker, str) and marker in gateway.submitted_content
    assert len(client.app.state.document_mutation_store.list()) == 1
    assert len(asyncio.run(gateway.list_documents("repo-remote"))) == 1

    gateway.discovery_available = True
    with _recreated_client(client, gateway) as recreated:
        assert recreated.app.state.document_mutation_store.list() == []

    assert gateway.create_calls == 1
    assert asyncio.run(gateway.list_documents("repo-remote")) == []


class LostDeleteAcknowledgementGateway(FakeYuqueGateway):
    def __init__(self) -> None:
        super().__init__()
        self.delete_calls = 0
        self.exists_checks = 0

    async def delete_document(self, document_id: str) -> None:
        self.delete_calls += 1
        if self.delete_calls == 1:
            await super().delete_document(document_id)
            raise ConnectionError("delete acknowledgement lost")
        await super().delete_document(document_id)

    async def document_exists(self, repository_id: str, document_id: str) -> bool:
        self.exists_checks += 1
        return await super().document_exists(repository_id, document_id)


def test_lost_delete_acknowledgement_converges_after_restart(client, auth_headers) -> None:
    """Verified remote absence must complete a retried create compensation."""
    gateway = LostDeleteAcknowledgementGateway()
    repository, _ = _install_gateway(client, gateway)
    vector_store = _install_vector_store(client)
    vector_store.fail_upsert = True

    failed = client.post(
        f"/api/repositories/{repository.id}/documents",
        headers=auth_headers,
        json={"title": "Rollback", "content": "# Rollback"},
    )

    assert failed.status_code == 503
    assert gateway.delete_calls == 1
    assert len(client.app.state.document_mutation_store.list()) == 1
    assert asyncio.run(gateway.list_documents("repo-remote")) == []

    with _recreated_client(client, gateway) as recreated:
        assert recreated.app.state.document_mutation_store.list() == []

    assert gateway.delete_calls == 2
    assert gateway.exists_checks == 1


def test_update_rollback_distinguishes_missing_file_from_empty_file(
    client, auth_headers
) -> None:
    """Rollback must restore absence as absence and an empty file as an empty file."""
    repository, _ = _install_gateway(client)
    _install_vector_store(client)
    created = [
        client.post(
            f"/api/repositories/{repository.id}/documents",
            headers=auth_headers,
            json={"title": title, "content": f"# {title}"},
        ).json()
        for title in ("Missing", "Empty")
    ]
    missing_path = Path(client.app.state.document_store.get(created[0]["id"]).markdown_path)
    empty_path = Path(client.app.state.document_store.get(created[1]["id"]).markdown_path)
    missing_path.unlink()
    empty_path.write_bytes(b"")
    original_update_editor = client.app.state.document_store.update_editor

    def fail_update_editor(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("metadata commit failed")

    client.app.state.document_store.update_editor = fail_update_editor
    try:
        responses = [
            client.put(
                f"/api/documents/{document['id']}",
                headers=auth_headers,
                json={"title": f"{document['title']} changed", "content": "# Changed"},
            )
            for document in created
        ]
    finally:
        client.app.state.document_store.update_editor = original_update_editor

    assert [response.status_code for response in responses] == [503, 503]
    assert not missing_path.exists()
    assert empty_path.exists() and empty_path.read_bytes() == b""


def test_update_rollback_restores_non_utf8_bytes_exactly(client, auth_headers) -> None:
    """The pre-mutation file snapshot is byte-exact rather than decoded text."""
    repository, _ = _install_gateway(client)
    _install_vector_store(client)
    created = client.post(
        f"/api/repositories/{repository.id}/documents",
        headers=auth_headers,
        json={"title": "Binary", "content": "# Binary"},
    ).json()
    document_id = created["id"]
    path = Path(client.app.state.document_store.get(document_id).markdown_path)
    previous = b"\xff\x00\r\nexact\x80"
    path.write_bytes(previous)
    original_update_editor = client.app.state.document_store.update_editor

    def fail_update_editor(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("metadata commit failed")

    client.app.state.document_store.update_editor = fail_update_editor
    try:
        response = client.put(
            f"/api/documents/{document_id}",
            headers=auth_headers,
            json={"title": "Changed", "content": "# Changed"},
        )
    finally:
        client.app.state.document_store.update_editor = original_update_editor

    assert response.status_code == 503
    assert path.read_bytes() == previous


def test_unreadable_snapshot_aborts_before_remote_update(
    client, auth_headers, monkeypatch
) -> None:
    """A file read error cannot be represented as empty content and sent remotely."""
    repository, gateway = _install_gateway(client)
    _install_vector_store(client)
    created = client.post(
        f"/api/repositories/{repository.id}/documents",
        headers=auth_headers,
        json={"title": "Unreadable", "content": "# Original"},
    ).json()
    document_id = created["id"]
    path = Path(client.app.state.document_store.get(document_id).markdown_path)
    remote_before = asyncio.run(gateway.read_document("doc-1"))
    original_read_bytes = Path.read_bytes

    def fail_selected_path(self):  # type: ignore[no-untyped-def]
        if self == path:
            raise PermissionError("snapshot denied")
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", fail_selected_path)
    response = client.put(
        f"/api/documents/{document_id}",
        headers=auth_headers,
        json={"title": "Changed", "content": "# Changed"},
    )

    assert response.status_code == 503
    assert asyncio.run(gateway.read_document("doc-1")) == remote_before
    assert client.app.state.document_mutation_store.list() == []


class RollbackBlockedGateway(FakeYuqueGateway):
    def __init__(self) -> None:
        super().__init__()
        self.update_calls = 0
        self.allow_rollback = False

    async def update_document(self, request):  # type: ignore[no-untyped-def]
        self.update_calls += 1
        if self.update_calls > 1 and not self.allow_rollback:
            raise ConnectionError("rollback unavailable")
        return await super().update_document(request)


def test_restart_removes_abandoned_stage_only_after_compensation_converges(
    client, auth_headers, monkeypatch
) -> None:
    """Mutation-owned staging survives an incomplete rollback and is cleaned on recovery."""
    gateway = RollbackBlockedGateway()
    repository, _ = _install_gateway(client, gateway)
    _install_vector_store(client)
    created = client.post(
        f"/api/repositories/{repository.id}/documents",
        headers=auth_headers,
        json={"title": "Stage", "content": "# Original"},
    ).json()
    document_id = created["id"]
    path = Path(client.app.state.document_store.get(document_id).markdown_path)

    with monkeypatch.context() as scoped:
        scoped.setattr("app.api.documents.os.replace", lambda *_: (_ for _ in ()).throw(OSError("replace failed")))
        failed = client.put(
            f"/api/documents/{document_id}",
            headers=auth_headers,
            json={"title": "Changed", "content": "# Changed"},
        )

    assert failed.status_code == 503
    records = client.app.state.document_mutation_store.list()
    assert len(records) == 1
    payload = json.loads(records[0].payload_json)
    stage_path = Path(payload["staged_path"])
    assert stage_path.exists()

    gateway.allow_rollback = True
    with _recreated_client(client, gateway) as recreated:
        assert recreated.app.state.document_mutation_store.list() == []

    assert not stage_path.exists()
    assert path.read_bytes() == b"# Original"
    assert asyncio.run(gateway.read_document("doc-1")).content == "# Original"


class BlockingMutationGateway(FakeYuqueGateway):
    def __init__(self) -> None:
        super().__init__()
        self.block_create = False
        self.block_update = False
        self.create_started = Event()
        self.create_release = Event()
        self.update_started = Event()
        self.update_release = Event()

    async def create_document(self, request):  # type: ignore[no-untyped-def]
        if self.block_create:
            self.create_started.set()
            released = await asyncio.to_thread(self.create_release.wait, 5)
            if not released:
                raise TimeoutError("create was never released")
        return await super().create_document(request)

    async def update_document(self, request):  # type: ignore[no-untyped-def]
        if self.block_update and request.title == "Changed":
            self.update_started.set()
            released = await asyncio.to_thread(self.update_release.wait, 5)
            if not released:
                raise TimeoutError("update was never released")
        return await super().update_document(request)


def test_concurrent_get_does_not_steal_live_create_intent(client, auth_headers) -> None:
    """Request-time recovery skips a create currently owned by another live request."""
    gateway = BlockingMutationGateway()
    repository, _ = _install_gateway(client, gateway)
    _install_vector_store(client)
    gateway.block_create = True

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            client.post,
            f"/api/repositories/{repository.id}/documents",
            headers=auth_headers,
            json={"title": "Live", "content": "# Live"},
        )
        assert gateway.create_started.wait(3)
        try:
            listing = client.get(
                f"/api/repositories/{repository.id}/documents", headers=auth_headers
            )
            assert listing.status_code == 200
            assert len(client.app.state.document_mutation_store.list()) == 1
        finally:
            gateway.create_release.set()
        created = future.result(timeout=5)

    assert created.status_code == 201
    assert client.app.state.document_mutation_store.list() == []


def test_concurrent_get_does_not_steal_live_update_intent(client, auth_headers) -> None:
    """Request-time recovery skips an update currently owned by another live request."""
    gateway = BlockingMutationGateway()
    repository, _ = _install_gateway(client, gateway)
    _install_vector_store(client)
    created = client.post(
        f"/api/repositories/{repository.id}/documents",
        headers=auth_headers,
        json={"title": "Original", "content": "# Original"},
    ).json()
    gateway.block_update = True

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            client.put,
            f"/api/documents/{created['id']}",
            headers=auth_headers,
            json={"title": "Changed", "content": "# Changed"},
        )
        assert gateway.update_started.wait(3)
        try:
            read = client.get(f"/api/documents/{created['id']}", headers=auth_headers)
            assert read.status_code == 200
            assert len(client.app.state.document_mutation_store.list()) == 1
        finally:
            gateway.update_release.set()
        updated = future.result(timeout=5)

    assert updated.status_code == 200
    assert updated.json()["content"] == "# Changed"
    assert client.app.state.document_mutation_store.list() == []


class BlockingDeleteGateway(FakeYuqueGateway):
    def __init__(self) -> None:
        super().__init__()
        self.delete_started = asyncio.Event()
        self.delete_release = asyncio.Event()

    async def delete_document(self, document_id: str) -> None:
        self.delete_started.set()
        await self.delete_release.wait()
        await super().delete_document(document_id)


async def _seed_pending_remote_create(app, gateway: BlockingDeleteGateway, repository_id: str):  # type: ignore[no-untyped-def]
    remote = await gateway.create_document(
        CreateYuqueDocumentRequest(
            repository_id="repo-remote",
            title="Pending",
            content="# Pending\n\n<!-- docmind-mutation:pending -->",
        )
    )
    app.state.document_mutation_store.create(
        operation="create",
        repository_id=repository_id,
        document_id=None,
        payload={
            "phase": "rollback",
            "remote_applied": True,
            "remote_id": remote.yuque_id,
            "remote_repository_id": "repo-remote",
            "marker": "docmind-mutation:pending",
            "requested_title": "Pending",
            "requested_content": "# Pending",
        },
    )


def test_request_time_recovery_propagates_cancellation_after_cleanup(client) -> None:
    """Cancellation of on-demand recovery is re-raised after its compensation finishes."""
    gateway = BlockingDeleteGateway()
    repository, _ = _install_gateway(client, gateway)
    client.app.state.yuque_gateway = gateway
    request = Request({"type": "http", "app": client.app})

    async def scenario() -> None:
        await _seed_pending_remote_create(client.app, gateway, repository.id)
        task = asyncio.create_task(list_documents(request, repository.id))
        await asyncio.wait_for(gateway.delete_started.wait(), 2)
        task.cancel()
        gateway.delete_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert client.app.state.document_mutation_store.list() == []
    assert asyncio.run(gateway.list_documents("repo-remote")) == []


def test_startup_recovery_propagates_cancellation_after_cleanup(tmp_path) -> None:
    """Cancellation of lifespan recovery is re-raised after its compensation finishes."""
    settings = AppSettings(
        session_token=SecretStr("startup-token"),
        data_dir=tmp_path / "startup-data",
        environment="test",
    )
    database_url = f"sqlite+pysqlite:///{settings.data_dir / 'database' / 'docmind.sqlite3'}"
    database = Database(database_url)
    database.upgrade()
    remote_gateway = BlockingDeleteGateway()
    remote_repository = remote_gateway.seed_repository("repo-remote", "SwiftUI")
    repository = RepositoryStore(database).upsert_remote(
        yuque_id=remote_repository.yuque_id,
        name=remote_repository.name,
        description=None,
        yuque_url=remote_repository.url,
    )
    database.engine.dispose()
    app = create_app(
        settings,
        secret_store=MemorySecretStore(),
        yuque_gateway=remote_gateway,
        embedding_provider=FakeEmbeddingProvider(settings.embedding_settings),
    )

    async def scenario() -> None:
        remote = await remote_gateway.create_document(
            CreateYuqueDocumentRequest(
                repository_id="repo-remote",
                title="Pending",
                content="# Pending\n\n<!-- docmind-mutation:startup -->",
            )
        )
        seeding_database = Database(database_url)
        DocumentMutationStore(seeding_database).create(
            operation="create",
            repository_id=repository.id,
            document_id=None,
            payload={
                "phase": "rollback",
                "remote_applied": True,
                "remote_id": remote.yuque_id,
                "remote_repository_id": "repo-remote",
                "marker": "docmind-mutation:startup",
                "requested_title": "Pending",
                "requested_content": "# Pending",
            },
        )
        seeding_database.engine.dispose()

        async def run_lifespan() -> None:
            async with app.router.lifespan_context(app):
                pass

        task = asyncio.create_task(run_lifespan())
        await asyncio.wait_for(remote_gateway.delete_started.wait(), 2)
        task.cancel()
        remote_gateway.delete_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    verification_database = Database(database_url)
    assert DocumentMutationStore(verification_database).list() == []
    verification_database.engine.dispose()
    assert asyncio.run(remote_gateway.list_documents("repo-remote")) == []
