from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from threading import Event

import pytest
from starlette.requests import Request

from app.api.imports import import_events
from app.document.formats import DocumentFormat
from app.imports.events import InMemoryEventBroker
from app.schemas.imports import ImportJobView, SourcePreview, SourceRef


def job_view(state: str = "pending", progress: int = 0) -> ImportJobView:
    now = datetime.now(UTC)
    return ImportJobView(
        id="job-api-1",
        source=SourceRef(kind="url", value="https://example.test/guide.md"),
        repository_id="repository-1",
        state=state,
        current_stage=state,
        progress=progress,
        message=state,
        retryable=state == "failed",
        cancel_requested=False,
        created_at=now,
        updated_at=now,
    )


class StubImportService:
    def __init__(self) -> None:
        self.event_broker = InMemoryEventBroker()
        self.jobs = {"job-api-1": job_view()}
        self.last_event_sequences = {"job-api-1": 0}
        self.run_started = Event()
        self.release_run = Event()
        self.inspect_calls = 0

    async def inspect(self, ref: SourceRef) -> SourcePreview:
        self.inspect_calls += 1
        return SourcePreview(
            title="Guide",
            source_kind=ref.kind,
            source_url=ref.value,
            media_type="text/markdown",
            size_bytes=12,
            fingerprint="a" * 64,
        )

    async def create(self, request):  # type: ignore[no-untyped-def]
        del request
        self.jobs["job-api-1"] = job_view()
        return self.jobs["job-api-1"]

    def get(self, job_id: str) -> ImportJobView:
        return self.jobs[job_id]

    async def run(self, job_id: str) -> None:
        self.run_started.set()
        await asyncio.to_thread(self.release_run.wait)
        self.jobs[job_id] = job_view("completed", 100)
        await self.event_broker.publish(job_id, "progress", {"progress": 70})
        await self.event_broker.publish(job_id, "done", {"progress": 100})

    async def retry(self, job_id: str) -> ImportJobView:
        await self.event_broker.reopen(job_id)
        self.jobs[job_id] = job_view("indexing", 70)
        return self.jobs[job_id]

    async def cancel(self, job_id: str) -> ImportJobView:
        self.jobs[job_id] = job_view("cancelled", 20)
        return self.jobs[job_id]

    async def ensure_terminal_event(self, job_id: str, job: ImportJobView) -> None:
        terminal = await self.event_broker.terminal(job_id)
        if terminal is not None:
            return
        sequence = self.last_event_sequences[job_id] + 1
        self.last_event_sequences[job_id] = sequence
        event_type = "error" if job.state == "failed" else "done"
        payload = {
            "progress": job.progress,
            "state": job.state,
            "message": job.error_message or job.message,
        }
        if event_type == "error":
            payload.update({"code": job.error_code, "retryable": job.retryable})
        await self.event_broker.publish(job_id, event_type, payload, sequence=sequence)


def test_import_routes_require_runtime_token(client) -> None:
    response = client.post(
        "/api/imports/inspect",
        json={"kind": "url", "value": "https://example.test/guide.md"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


def test_inspect_returns_fingerprint_without_creating_background_task(client, auth_headers) -> None:
    service = StubImportService()
    client.app.state.import_service = service

    response = client.post(
        "/api/imports/inspect",
        headers=auth_headers,
        json={"kind": "url", "value": "https://example.test/guide.md"},
    )

    assert response.status_code == 200
    assert response.json()["fingerprint"] == "a" * 64
    assert service.inspect_calls == 1
    assert client.app.state.import_tasks == set()


def test_create_retains_background_task_until_runner_finishes(client, auth_headers) -> None:
    service = StubImportService()
    client.app.state.import_service = service

    response = client.post(
        "/api/imports",
        headers=auth_headers,
        json={
            "source": {"kind": "url", "value": "https://example.test/guide.md"},
            "repositoryId": "repository-1",
            "fingerprint": "a" * 64,
            "duplicateDecision": None,
        },
    )

    assert response.status_code == 202
    assert response.json()["id"] == "job-api-1"
    assert len(client.app.state.import_tasks) == 1
    for _ in range(100):
        if service.run_started.is_set():
            break
        time.sleep(0.001)
    assert service.run_started.is_set()
    service.release_run.set()
    for _ in range(100):
        if not client.app.state.import_tasks:
            break
        time.sleep(0.001)
    assert client.app.state.import_tasks == set()


def test_import_events_replay_after_last_event_id_and_close_on_terminal(
    client, auth_headers
) -> None:
    service = StubImportService()
    client.app.state.import_service = service
    asyncio.run(service.event_broker.publish("job-api-1", "progress", {"progress": 20}))
    asyncio.run(service.event_broker.publish("job-api-1", "done", {"progress": 100}))

    response = client.get(
        "/api/imports/job-api-1/events",
        headers={**auth_headers, "Last-Event-ID": "1"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.count("id: ") == 1
    assert "id: 2\n" in response.text
    assert '"sequence":2' in response.text
    assert '"type":"done"' in response.text


def test_retry_starts_owned_runner_and_cancel_returns_updated_view(client, auth_headers) -> None:
    service = StubImportService()
    service.jobs["job-api-1"] = job_view("failed", 70)
    client.app.state.import_service = service

    retried = client.post("/api/imports/job-api-1/retry", headers=auth_headers)
    cancelled = client.post("/api/imports/job-api-1/cancel", headers=auth_headers)

    assert retried.status_code == 202
    assert retried.json()["state"] == "indexing"
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "cancelled"
    assert len(client.app.state.import_tasks) == 1
    service.release_run.set()


async def test_events_close_for_persisted_failure_after_broker_restart(client) -> None:
    service = StubImportService()
    service.jobs["job-api-1"] = job_view("failed", 70)
    client.app.state.import_service = service
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/imports/job-api-1/events",
            "headers": [],
            "app": client.app,
        }
    )

    response = await import_events(request, "job-api-1", None)
    frame = await asyncio.wait_for(anext(response.body_iterator), timeout=0.1)

    assert '"type":"error"' in frame
    with pytest.raises(StopAsyncIteration):
        await anext(response.body_iterator)


@pytest.mark.parametrize("cursors", [("6", "100"), ("100", "6")])
async def test_persisted_terminal_uses_durable_sequence_after_broker_restart(
    client, cursors: tuple[str, str]
) -> None:
    service = StubImportService()
    service.jobs["job-api-1"] = job_view("failed", 70)
    service.last_event_sequences["job-api-1"] = 100
    client.app.state.import_service = service
    for cursor in cursors:
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/imports/job-api-1/events",
                "headers": [(b"last-event-id", cursor.encode())],
                "app": client.app,
            }
        )

        response = await import_events(request, "job-api-1", cursor)
        frame = await asyncio.wait_for(anext(response.body_iterator), timeout=0.1)

        assert frame.startswith("id: 101\n")
        assert '"sequence":101' in frame
        assert '"type":"error"' in frame
        with pytest.raises(StopAsyncIteration):
            await anext(response.body_iterator)


# -- the format catalogue the client builds its picker from -----------------


def test_formats_endpoint_lists_every_pickable_format(client, auth_headers) -> None:
    response = client.get("/api/imports/formats", headers=auth_headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert [entry["name"] for entry in body] == ["pdf", "markdown", "docx"]
    # camelCase over the wire; the renderer never sees snake_case.
    assert set(body[0]) == {"name", "label", "extensions", "mediaType", "maxBytes"}
    assert body[0]["mediaType"] == "application/pdf"
    assert body[0]["extensions"] == [".pdf"]


def test_formats_endpoint_omits_formats_a_user_may_not_pick_alone(client, auth_headers) -> None:
    response = client.get("/api/imports/formats", headers=auth_headers)

    names = [entry["name"] for entry in response.json()]
    # HTML is parsable and allowed inside an imported directory, but a lone
    # .html is not offered — this is the distinction `single_file` encodes.
    assert "html" not in names


def test_formats_endpoint_carries_the_configured_ceiling(client, auth_headers) -> None:
    body = client.get("/api/imports/formats", headers=auth_headers).json()
    limits = {entry["name"]: entry["maxBytes"] for entry in body}

    # The ceilings come from settings, and binary formats use the wider one —
    # a client must not be left deriving that from the media type.
    assert limits["pdf"] > limits["markdown"]
    assert limits["docx"] == limits["pdf"]


def test_formats_endpoint_follows_a_plugin_registered_format(client, auth_headers) -> None:
    """The promise the picker makes: a new format appears with no client edit."""

    registry = client.app.state.format_registry
    registry.register(
        DocumentFormat(
            name="tex",
            label="TeX 文档",
            media_type="text/x-tex",
            extensions=(".tex",),
            parse=lambda document: document,  # type: ignore[arg-type,return-value]
        )
    )

    body = client.get("/api/imports/formats", headers=auth_headers).json()

    assert [entry["name"] for entry in body] == ["pdf", "markdown", "docx", "tex"]
    assert body[-1]["label"] == "TeX 文档"
    assert body[-1]["extensions"] == [".tex"]


def test_formats_endpoint_requires_a_token(client) -> None:
    assert client.get("/api/imports/formats").status_code == 401
