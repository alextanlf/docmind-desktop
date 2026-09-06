from __future__ import annotations

import hashlib
import socket
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.config import AppSettings
from app.core.embedding import FakeEmbeddingProvider
from app.core.llm import ChatDelta
from app.core.secrets import MemorySecretStore
from app.main import create_app
from app.storage.models import DistillationRecord, MemoryChunkRecord, SessionRecord
from app.yuque.gateway import FakeYuqueGateway
from tests.integration.conftest import TestAppHarness

TOKEN = "memory-acceptance-token"
SUMMARY = "# summarybeacon\n- 会话决定：保留离线检索能力。"
EDITED = "# editedbeacon\n- 经人工确认：本地保存后可以跨会话检索。"
NOW = datetime(2040, 1, 2, 12, tzinfo=UTC)


class DeterministicEmbedding(FakeEmbeddingProvider):
    fail_memory = False

    def vector(self, text):
        coordinate = 1 if "editedbeacon" in text else 2 if "summarybeacon" in text else 0
        return [float(index == coordinate) for index in range(self.settings.dimension)]

    async def embed_documents(self, texts):
        if self.fail_memory and any("editedbeacon" in text for text in texts):
            raise RuntimeError("deterministic embedding failure")
        return [self.vector(text) for text in texts]

    async def embed_query(self, text):
        return self.vector(text)


class DeterministicLLM:
    def __init__(self):
        self.prompts = []

    async def stream_chat(self, request):
        prompt = "\n".join(message.content for message in request.messages)
        self.prompts.append(prompt)
        if prompt.startswith("总结以下"):
            content = SUMMARY
        elif prompt.startswith("请将会话蒸馏"):
            content = "# 草稿\n- 离线知识来自会话 [S1]"
        elif "<memory-context>" in prompt:
            content = "检索到已确认的记忆 [M1]"
        else:
            content = "离线文档支持本地检索 [S1]"
        yield ChatDelta(content=content)


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    attempts = []
    connect = socket.socket.connect

    def offline_connect(connection, address):
        if connection.family in {socket.AF_INET, socket.AF_INET6}:
            attempts.append(address)
            raise AssertionError("memory acceptance must not connect to the network")
        return connect(connection, address)

    monkeypatch.setattr(socket.socket, "connect", offline_connect)
    monkeypatch.setenv("ANONYMIZED_TELEMETRY", "False")
    monkeypatch.setenv("DOCMIND_FAKE_SERVICES", "1")
    monkeypatch.delenv("DOCMIND_E2E", raising=False)
    settings = AppSettings(
        session_token=SecretStr(TOKEN), data_dir=tmp_path / "memory", environment="test"
    )
    yield settings
    assert attempts == []


@asynccontextmanager
async def running(settings):
    embedding = DeterministicEmbedding(settings.embedding_settings)
    await embedding.ensure_ready()
    llm = DeterministicLLM()
    app = create_app(
        settings,
        secret_store=MemorySecretStore(),
        embedding_provider=embedding,
        llm_provider=llm,
        yuque_gateway=FakeYuqueGateway(),
    )
    async with app.router.lifespan_context(app):
        app.state.summary_scheduler.stop()
        app.state.summary_service.clock = lambda: NOW
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://memory.test",
            headers={"X-DocMind-Token": TOKEN},
        ) as client:
            yield TestAppHarness(client, app), embedding, llm


async def repository(harness, name):
    repo = await harness.create_repository(name)
    staged_id = str(uuid4())
    staged_path = harness.app.state.settings.staging_dir / f"{staged_id}.md"
    staged_path.write_text("# 离线文档\n\n离线知识使用本地检索。", encoding="utf-8")
    preview = await harness.inspect_staged(staged_id)
    job = await harness.create_import(preview, repo.id)
    await harness.wait_for_import(job.id)
    return repo.id


async def snapshot(harness, path):
    response = await harness.client.get(path)
    assert response.status_code == 200, response.text
    return response.json()


async def prepare(harness, trigger):
    repo_id = await repository(harness, "记忆来源")
    session = await harness.create_session([repo_id])
    events = await harness.ask(session.id, "离线知识如何检索？")
    assert events[-1].type == "done"
    messages = await snapshot(harness, f"/api/sessions/{session.id}/messages")
    assert messages[-1]["citations"][0]["sourceId"] == "S1"
    service = harness.app.state.summary_service
    with harness.app.state.database.session() as database:
        parent = database.get(SessionRecord, session.id)
        assert parent.summary_due_at == NOW + timedelta(minutes=30)
    if trigger == "ended":
        response = await harness.client.post(f"/api/sessions/{session.id}/end")
        assert response.status_code == 200, response.text
        assert response.json()["endedAt"] is not None
    else:
        assert await service.run_due(NOW + timedelta(minutes=30, microseconds=-1)) == 0
        assert await snapshot(harness, f"/api/sessions/{session.id}/summary") is None
        service.clock = lambda: NOW + timedelta(minutes=30)
        assert await service.run_due() == 1
        assert await service.run_due() == 0
        with harness.app.state.database.session() as database:
            assert database.get(SessionRecord, session.id).ended_at is None
    summary = await snapshot(harness, f"/api/sessions/{session.id}/summary")
    assert summary["state"] == "ready"
    assert summary["content"] == SUMMARY
    assert summary["repositoryIds"] == [repo_id]
    await assert_reference(harness, repo_id, "summarybeacon", summary["id"], session.id,
                           "session_summary", SUMMARY)
    response = await harness.client.post(f"/api/sessions/{session.id}/distillations")
    assert response.status_code == 200, response.text
    draft = response.json()
    assert draft["state"] == "draft"
    assert draft["sources"] == messages[-1]["citations"]
    response = await harness.client.put(
        f"/api/distillations/{draft['id']}",
        json={"title": "人工确认", "content": EDITED, "keyPoints": ["跨会话检索"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["content"] == EDITED
    assert response.json()["keyPoints"] == ["跨会话检索"]
    assert await harness.app.state.memory_retriever.search("editedbeacon", [repo_id]) == []
    return repo_id, session.id, draft["id"]


async def assert_reference(harness, repo_id, query, source_id, origin, kind, content):
    hits = await harness.app.state.memory_retriever.search(query, [repo_id])
    assert len(hits) == 1
    assert hits[0].source_id == source_id
    assert hits[0].text == content
    session = await harness.create_session([repo_id])
    assert session.id != origin
    events = await harness.ask(session.id, query)
    assert events[-1].type == "done"
    citations = next(event.payload["citations"] for event in events if event.type == "citations")
    assert len(citations) == 1
    citation = citations[0]
    assert citation["sourceId"] == "M1"
    assert citation["kind"] == "memory"
    assert citation["memoryKind"] == kind
    assert citation["memoryId"] == source_id
    assert citation["sessionId"] == origin
    assert citation["excerpt"] == content
    assert "[M1]" in "".join(event.payload.get("content", "") for event in events)
    messages = await snapshot(harness, f"/api/sessions/{session.id}/messages")
    assert messages[-1]["citations"] == citations
    assert messages[-1]["generationStatus"] == "completed"


async def save(harness, identifier):
    response = await harness.client.post(
        f"/api/distillations/{identifier}/save", json={"target": "local"}
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("trigger", ["ended", "idle"])
async def test_memory_lifecycle_local_save_scope_and_restart(runtime, trigger):
    async with running(runtime) as (harness, _, llm):
        repo_id, origin, identifier = await prepare(harness, trigger)
        distillation_prompt = next(prompt for prompt in llm.prompts if prompt.startswith("请将会话蒸馏"))
        assert SUMMARY in distillation_prompt
        saved = await save(harness, identifier)
        assert saved["state"] == "saved"
        local_path = runtime.data_dir / saved["localPath"]
        assert local_path.read_text(encoding="utf-8") == EDITED
        assert not list(local_path.parent.glob("*.partial"))
        with harness.app.state.database.session() as database:
            record = database.get(DistillationRecord, identifier)
            assert record.content_hash == hashlib.sha256(EDITED.encode()).hexdigest()
            owners = list(database.scalars(select(MemoryChunkRecord).where(
                MemoryChunkRecord.distillation_id == identifier
            )))
            assert len(owners) == 1 and owners[0].indexed
            assert owners[0].repository_id == repo_id
        await assert_reference(harness, repo_id, "editedbeacon", identifier, origin,
                               "distillation", EDITED)
        other_repo = await repository(harness, "隔离知识库")
        assert await harness.app.state.memory_retriever.search("editedbeacon", [other_repo]) == []
        other = await harness.create_session([other_repo])
        events = await harness.ask(other.id, "editedbeacon")
        assert events[-1].type == "done"
        assert next(event.payload["citations"] for event in events if event.type == "citations") == []
        assert "[M1]" not in "".join(event.payload.get("content", "") for event in events)
    async with running(runtime) as (rebuilt, _, _):
        saved_again = await snapshot(rebuilt, f"/api/distillations/{identifier}")
        assert saved_again["state"] == "saved"
        assert saved_again["localPath"] == saved["localPath"]
        await assert_reference(rebuilt, repo_id, "editedbeacon", identifier, origin,
                               "distillation", EDITED)
        assert await rebuilt.app.state.memory_retriever.search("editedbeacon", [other_repo]) == []
        summary = await snapshot(rebuilt, f"/api/sessions/{origin}/summary")
        summary_hits = await rebuilt.app.state.memory_retriever.search("summarybeacon", [repo_id])
        assert [hit.source_id for hit in summary_hits] == [summary["id"]]
        response = await rebuilt.client.delete(f"/api/sessions/{origin}/summary")
        assert response.status_code == 204
        response = await rebuilt.client.delete(f"/api/distillations/{identifier}")
        assert response.status_code == 204
        assert await rebuilt.app.state.memory_retriever.search("editedbeacon", [repo_id]) == []
        assert await rebuilt.app.state.memory_retriever.search("summarybeacon", [repo_id]) == []
    async with running(runtime) as (cleaned, embedding, _):
        assert cleaned.app.state.memory_store.list_vector_cleanups() == []
        vectors = cleaned.app.state.memory_indexer.vector_store
        assert vectors.query_memory(
            "distilled_knowledge", await embedding.embed_query("editedbeacon"), 5,
            repository_id=repo_id,
        ) == []
        assert vectors.query_memory(
            "session_summaries", await embedding.embed_query("summarybeacon"), 5,
            repository_id=repo_id,
        ) == []


@pytest.mark.parametrize("recovery", ["retry", "restart"])
async def test_saved_unindexed_preserves_file_and_recovers(runtime, recovery):
    async with running(runtime) as (harness, embedding, _):
        repo_id, origin, identifier = await prepare(harness, "ended")
        embedding.fail_memory = True
        saved = await save(harness, identifier)
        assert saved["state"] == "saved_unindexed"
        assert saved["errorCode"] == "MEMORY_INDEX_FAILED"
        assert saved["retryable"] is True
        path = runtime.data_dir / saved["localPath"]
        assert path.read_text(encoding="utf-8") == EDITED
        stat = path.stat()
        assert await harness.app.state.memory_retriever.search("editedbeacon", [repo_id]) == []
        waiting_session = await harness.create_session([repo_id])
        events = await harness.ask(waiting_session.id, "editedbeacon")
        assert events[-1].type == "done"
        assert next(
            event.payload["citations"] for event in events if event.type == "citations"
        ) == []
        waiting_messages = await snapshot(
            harness, f"/api/sessions/{waiting_session.id}/messages"
        )
        assert waiting_messages[-1]["citations"] == []
        with harness.app.state.database.session() as database:
            owners = list(database.scalars(select(MemoryChunkRecord).where(
                MemoryChunkRecord.distillation_id == identifier
            )))
            assert len(owners) == 1 and not owners[0].indexed
        if recovery == "retry":
            embedding.fail_memory = False
            recovered = await save(harness, identifier)
            assert recovered["state"] == "saved"
            assert recovered["errorCode"] is None
            assert recovered["retryable"] is False
            await assert_reference(harness, repo_id, "editedbeacon", identifier, origin,
                                   "distillation", EDITED)
    async with running(runtime) as (rebuilt, _, _):
        recovered = await snapshot(rebuilt, f"/api/distillations/{identifier}")
        assert recovered["state"] == "saved"
        assert recovered["errorCode"] is None
        assert recovered["retryable"] is False
        assert path.stat().st_mtime_ns == stat.st_mtime_ns
        assert path.stat().st_ino == stat.st_ino
        assert path.read_text(encoding="utf-8") == EDITED
        await assert_reference(rebuilt, repo_id, "editedbeacon", identifier, origin,
                               "distillation", EDITED)


async def test_new_activity_excludes_stale_summary_until_regenerated(runtime):
    async with running(runtime) as (harness, _, llm):
        repo_id, origin, identifier = await prepare(harness, "idle")
        summary = await snapshot(harness, f"/api/sessions/{origin}/summary")
        events = await harness.ask(origin, "补充离线检索的决定")
        assert events[-1].type == "done"
        stale = await snapshot(harness, f"/api/sessions/{origin}/summary")
        assert stale["id"] == summary["id"]
        assert stale["state"] == "stale"
        assert await harness.app.state.memory_retriever.search("summarybeacon", [repo_id]) == []
        with harness.app.state.database.session() as database:
            parent = database.get(SessionRecord, origin)
            assert parent.ended_at is None
            assert parent.summary_due_at == NOW + timedelta(minutes=60)
        response = await harness.client.post(f"/api/distillations/{identifier}/regenerate")
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "draft"
        assert SUMMARY not in llm.prompts[-1]
        response = await harness.client.post(f"/api/sessions/{origin}/summary/regenerate")
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "ready"
        await assert_reference(
            harness, repo_id, "summarybeacon", response.json()["id"], origin,
            "session_summary", SUMMARY,
        )
