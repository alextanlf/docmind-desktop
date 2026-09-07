import asyncio

import httpx
import pytest

from app.core.ollama_service import OllamaService, run_pull_worker
from app.schemas.ollama import OllamaConfig
from app.storage.database import Database
from app.storage.repositories import OllamaPullStore


class ServiceResolver:
    def __init__(self, answers): self.answers = answers
    async def resolve(self, host): return self.answers


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


@pytest.mark.asyncio
async def test_models_normalize_tags_response():
    def handler(request):
        return httpx.Response(200, json={"models": [{"name": "qwen2.5:7b", "size": 12, "digest": "sha256:x"}]})
    service = OllamaService("http://127.0.0.1:11434", transport=httpx.MockTransport(handler))
    result = await service.models()
    assert result.available is True
    assert result.models[0].name == "qwen2.5:7b"

@pytest.mark.asyncio
async def test_models_reject_dns_rebinding_before_transport():
    service = OllamaService("http://localhost:11434", transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"models": []})), resolver=ServiceResolver(["10.0.0.1"]))
    result = await service.models()
    assert result.available is False

@pytest.mark.asyncio
async def test_unavailable_models_are_safe_snapshot():
    def handler(request):
        raise httpx.ConnectError("offline")
    result = await OllamaService("http://127.0.0.1:11434", transport=httpx.MockTransport(handler)).models()
    assert result.available is False and result.models == []


@pytest.mark.asyncio
async def test_status_caches_health_and_fetches_version_only_once():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "qwen2.5:7b",
                            "digest": "sha256:x",
                            "size": 12,
                            "modified_at": "2026-09-06T00:00:00Z",
                            "details": {"family": "qwen2"},
                        }
                    ]
                },
            )
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.5.4"})
        return httpx.Response(404)

    clock = FakeClock()
    service = OllamaService(
        config=OllamaConfig(model="qwen2.5:7b"),
        transport=httpx.MockTransport(handler),
        clock=clock,
    )
    first = await service.status()
    second = await service.status()
    assert first.available is True
    assert first.version == "0.5.4"
    assert first.selected_model_installed is True
    assert second.checked_at == first.checked_at
    assert calls.count("/api/tags") == 1
    assert calls.count("/api/version") == 1

    clock.advance(5.1)
    await service.status()
    assert calls.count("/api/tags") == 2


@pytest.mark.asyncio
async def test_invalidate_health_forces_refresh_and_malformed_models_are_safe():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "qwen2.5:7b", "size": "unknown", "modified_at": "bad"},
                        {"name": "\nnot-safe"},
                        {"digest": "missing-name"},
                    ]
                },
            )
        return httpx.Response(200, json={"version": "0.5.4"})

    service = OllamaService(
        config=OllamaConfig(), transport=httpx.MockTransport(handler)
    )
    models = await service.models()
    assert models.available is True
    assert [model.name for model in models.models] == ["qwen2.5:7b"]
    assert models.models[0].size_bytes is None
    service.invalidate_health()
    await service.status()
    assert calls == 3

def test_pull_survives_service_recreation(tmp_path):
    db = Database(f"sqlite+pysqlite:///{tmp_path/'db.sqlite'}"); db.upgrade(); store = OllamaPullStore(db)
    first = OllamaService("http://127.0.0.1:11434", store=store).create_pull("m")
    second = OllamaService("http://127.0.0.1:11434", store=store)
    assert second.get_pull(first.id).model_name == "m"

def test_duplicate_active_pull_is_reused(tmp_path):
    db = Database(f"sqlite+pysqlite:///{tmp_path/'db.sqlite'}"); db.upgrade(); store = OllamaPullStore(db)
    service = OllamaService("http://127.0.0.1:11434", store=store)
    first = service.create_pull("m"); second = service.create_pull("m")
    assert first.id == second.id

def test_cancel_event_sequence_matches_snapshot(tmp_path):
    db = Database(f"sqlite+pysqlite:///{tmp_path/'db.sqlite'}"); db.upgrade(); store = OllamaPullStore(db)
    service = OllamaService("http://127.0.0.1:11434", store=store)
    pull = service.create_pull("m")
    cancelled = service.cancel_pull(pull.id)
    event = service._events[pull.id][-1]
    assert event["sequence"] == cancelled.last_event_sequence
    assert event["payload"]["lastEventSequence"] == event["sequence"]

def test_retry_pull_creates_new_task_for_retryable_failure(tmp_path):
    db = Database(f"sqlite+pysqlite:///{tmp_path/'db.sqlite'}"); db.upgrade(); store = OllamaPullStore(db)
    service = OllamaService("http://127.0.0.1:11434", store=store)
    first = service.create_pull("m")
    row = store.get(str(first.id)); row.state, row.error_code, row.retryable = "failed", "OLLAMA_PULL_FAILED", True; store.save(row)
    retried = service.retry_pull(first.id)
    assert retried.id != first.id and retried.model_name == "m" and retried.state == "queued"

@pytest.mark.asyncio
async def test_execute_pull_projects_coordinator_events_and_persists(tmp_path):
    db = Database(f"sqlite+pysqlite:///{tmp_path/'db.sqlite'}"); db.upgrade(); store = OllamaPullStore(db)
    service = OllamaService("http://127.0.0.1:11434", store=store)
    pull = service.create_pull("m")

    async def events(_model):
        yield {"status": "下载中", "progress": 40}
        yield {"status": "完成", "progress": 100, "state": "completed"}

    class FakeCoordinator:
        pull = staticmethod(events)

    result = await service.execute_pull(pull.id, FakeCoordinator())
    assert result.state == "completed" and result.progress == 100
    persisted = store.get(str(pull.id))
    assert persisted.state == "completed" and persisted.progress == 100
    assert len(service._events[pull.id]) == 4

@pytest.mark.asyncio
async def test_start_pull_tracks_task_until_idle(tmp_path):
    db = Database(f"sqlite+pysqlite:///{tmp_path/'db.sqlite'}"); db.upgrade(); store = OllamaPullStore(db)
    service = OllamaService("http://127.0.0.1:11434", store=store)
    async def events(_model):
        yield {"status": "完成", "progress": 100, "state": "completed"}
    class FakeCoordinator:
        pull = staticmethod(events)
    service.coordinator = FakeCoordinator()
    pull = service.create_pull("m")
    task = service.start_pull(pull.id)
    assert task is not None
    await service.wait_for_pull(pull.id)
    assert service.get_pull(pull.id).state == "completed"

@pytest.mark.asyncio
async def test_cancel_running_pull_cancels_coordinator_task(tmp_path):
    db = Database(f"sqlite+pysqlite:///{tmp_path/'db.sqlite'}"); db.upgrade(); store = OllamaPullStore(db)
    service = OllamaService("http://127.0.0.1:11434", store=store)
    closed = False
    async def events(_model):
        nonlocal closed
        try:
            await asyncio.sleep(60)
            yield {"status": "never"}
        finally:
            closed = True
    class FakeCoordinator:
        pull = staticmethod(events)
    service.coordinator = FakeCoordinator()
    pull = service.create_pull("m")
    service.start_pull(pull.id)
    await asyncio.sleep(0)
    cancelled = service.cancel_pull(pull.id)
    await service.wait_for_pull(pull.id)
    assert cancelled.state == "cancelled" and closed is True

@pytest.mark.asyncio
async def test_run_queued_once_starts_pending_pull(tmp_path):
    db = Database(f"sqlite+pysqlite:///{tmp_path/'db.sqlite'}"); db.upgrade(); store = OllamaPullStore(db)
    service = OllamaService("http://127.0.0.1:11434", store=store)
    async def events(_model):
        yield {"status": "完成", "progress": 100, "state": "completed"}
    class FakeCoordinator:
        pull = staticmethod(events)
    service.coordinator = FakeCoordinator()
    pull = service.create_pull("m")
    started = service.run_queued_once()
    assert started == [pull.id]
    await service.wait_for_pull(pull.id)
    assert service.get_pull(pull.id).state == "completed"

@pytest.mark.asyncio
async def test_pull_worker_stops_without_extra_poll_after_event():
    class FakeService:
        def __init__(self): self.calls = 0
        def run_queued_once(self): self.calls += 1
    service, stop = FakeService(), asyncio.Event()
    async def stop_soon():
        await asyncio.sleep(0.01)
        stop.set()
    await asyncio.gather(run_pull_worker(service, stop, interval=0.001), stop_soon())
    assert service.calls > 0
