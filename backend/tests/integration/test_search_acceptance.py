from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from app.core.llm import ChatDelta, ModelConnectionResult
from app.search.tavily import TavilyProvider
from app.storage.models import (
    DocumentChunkRecord,
    DocumentRecord,
    ImportJobRecord,
    WebSearchRunRecord,
)

MODEL_KEY = "synthetic-model-acceptance-key"
SEARCH_KEY = "synthetic-search-acceptance-key"
QUESTION = "quasar neutrino spectroscopy unknown evidence"


class AcceptanceLLM:
    def __init__(self):
        self.requests = []

    async def stream_chat(self, request):
        self.requests.append(request)
        yield ChatDelta(content="Supported [W1]. Unsupported [W999].")

    async def test_connection(self):
        return ModelConnectionResult(connected=True, latency_ms=0)


@pytest.fixture(autouse=True)
def no_real_http(monkeypatch):
    async def reject_network(*args, **kwargs):
        pytest.fail("Unexpected real HTTP request")

    def reject_sync_network(*args, **kwargs):
        pytest.fail("Unexpected real synchronous HTTP request")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", reject_network)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", reject_sync_network)


@pytest.fixture
async def search_app(test_app, staged_markdown, monkeypatch):
    state = test_app.app.state
    wire = SimpleNamespace(calls=[], fail=False, model_keys=[], public=[])

    async def record_public_response(response):
        await response.aread()
        wire.public.append(response.text)

    test_app.client.event_hooks["response"].append(record_public_response)

    def respond(request):
        assert str(request.url) == "https://tavily.fixture/search"
        assert request.method == "POST"
        payload = json.loads(request.content)
        wire.calls.append(payload)
        if wire.fail:
            raise httpx.ConnectError(f"provider failed {SEARCH_KEY} {MODEL_KEY}", request=request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": f"https://evidence.test/{name}",
                        "title": name,
                        "snippet": f"{name} evidence",
                        "content": f"# {name}\n\n{name} spectral evidence.",
                    }
                    for name in ("SelectedQuasar", "SkippedNebula", "UnpickedPulsar")
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://tavily.fixture"
    ) as provider_client:

        def provider_factory(api_key):
            return TavilyProvider(api_key, client=provider_client)

        monkeypatch.setattr("app.main.TavilyProvider", provider_factory)
        monkeypatch.setattr("app.api.settings.TavilyProvider", provider_factory)
        llm = AcceptanceLLM()
        state.chat_service.llm = llm

        def model_factory(config, api_key):
            wire.model_keys.append(api_key)
            return llm

        state.settings_service.provider_factory = model_factory
        repository = await test_app.create_repository("search acceptance")
        preview = await test_app.inspect_staged(staged_markdown)
        job = await test_app.create_import(preview, repository.id)
        await test_app.wait_for_import(job.id)
        session = await test_app.create_session([repository.id])
        retrieval = await state.chat_service.retriever.search(QUESTION, [repository.id])
        assert not retrieval.hits
        yield SimpleNamespace(
            harness=test_app,
            state=state,
            wire=wire,
            llm=llm,
            repository=repository,
            session=session,
        )


async def settings(context, mode, key=SEARCH_KEY):
    response = await context.harness.client.put(
        "/api/settings/web-search", json={"mode": mode, "maxResults": 3, "apiKey": key}
    )
    assert response.status_code == 200, response.text
    context.wire.public.append(response.text)
    return response.json()


async def stream(context, *, continuation=None, permission="inherit"):
    path = f"/api/sessions/{context.session.id}/messages"
    body = {"requestId": str(uuid4()), "repositoryIds": [context.repository.id]}
    if continuation:
        path += f"/{continuation}/web-search/stream"
    else:
        path += "/stream"
        body.update(message=QUESTION, webSearchPermission=permission)
    response = await context.harness.client.post(path, json=body)
    assert response.status_code == 200, response.text
    context.wire.public.append(response.text)
    events = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    assert events[-1]["type"] in {"done", "error"}
    return events


def rows(context, model):
    with context.state.database.session() as session:
        return list(session.scalars(select(model)))


def assert_no_secrets(context, caplog):
    with context.state.database.engine.connect() as connection:
        dump = "\n".join(connection.connection.driver_connection.iterdump())
    public = "\n".join(context.wire.public) + caplog.text + dump
    for secret in (MODEL_KEY, SEARCH_KEY):
        assert secret not in public


@pytest.mark.parametrize(
    ("mode", "permission", "key", "suggested"),
    [
        ("off", "inherit", SEARCH_KEY, False),
        ("ask", "inherit", SEARCH_KEY, True),
        ("auto", "off", SEARCH_KEY, False),
        ("auto", "inherit", "", False),
    ],
)
async def test_unauthorized_modes_make_zero_provider_calls(
    search_app, mode, permission, key, suggested
):
    context = search_app
    await settings(context, mode, key)
    events = await stream(context, permission=permission)
    assert events[-1]["type"] == "done", events[-1]
    assert events[-1]["payload"]["searchSuggested"] is suggested
    assert context.wire.calls == []
    assert context.llm.requests == []
    assert rows(context, WebSearchRunRecord) == []


async def explicit_results(context):
    await settings(context, "ask")
    initial = await stream(context)
    original_id = initial[-1]["payload"]["userMessageId"]
    events = await stream(context, continuation=original_id)
    assert events[-1]["type"] == "done", events[-1]
    assert len(context.wire.calls) == 1
    messages = context.state.conversation_store.list_messages(context.session.id)
    assert [message.id for message in messages if message.role == "user"] == [original_id]
    answer = messages[-1]
    assert "[W999]" in answer.content
    citations = json.loads(answer.citations_json)
    assert [citation["sourceId"] for citation in citations] == ["W1"]
    assert citations[0]["kind"] == "web"
    run_id = citations[0]["searchRunId"]
    prompt = context.llm.requests[-1].messages[-1].content
    assert "<web-context>" in prompt and "[W1]" in prompt
    assert "不可信" in prompt
    run = rows(context, WebSearchRunRecord)[0]
    assert run.id == run_id and run.user_message_id == original_id
    response = await context.harness.client.get(
        f"/api/search/runs/{run_id}/results", params={"session_id": context.session.id}
    )
    assert response.status_code == 200, response.text
    results = response.json()
    assert len(results) == 3
    assert citations[0]["resultId"] == results[0]["id"]
    assert isinstance(citations[0]["retrievedAt"], str)
    response = await context.harness.client.get(f"/api/sessions/{context.session.id}/messages")
    assert response.status_code == 200, response.text
    assert response.json()[-1]["citations"] == citations
    return run_id, results


async def test_explicit_continuation_persists_only_registered_w_citations(search_app, caplog):
    await explicit_results(search_app)
    assert_no_secrets(search_app, caplog)


async def test_explicit_w_citation_then_partial_second_confirmation(search_app, caplog):
    context = search_app
    before_jobs = {row.id for row in rows(context, ImportJobRecord)}
    before_docs = {row.id for row in rows(context, DocumentRecord)}
    before_chunks = {row.id for row in rows(context, DocumentChunkRecord)}
    collection = context.state.vector_store.client.get_collection(
        context.state.vector_store.collection_name(context.repository.id)
    )
    before_vectors = set(collection.get()["ids"])
    run_id, results = await explicit_results(context)
    response = await context.harness.client.post(
        f"/api/web-search/runs/{run_id}/import-batch",
        json={
            "repositoryId": context.repository.id,
            "resultIds": [result["id"] for result in results[:2]],
        },
    )
    assert response.status_code == 201, response.text
    batch = response.json()
    assert batch["state"] == "awaiting_confirmation"
    items = await context.harness.all_batch_items(batch["id"])
    assert len(items.items) == 2
    assert all(item.import_job_id is None for item in items.items)
    assert {row.id for row in rows(context, ImportJobRecord)} == before_jobs
    assert {row.id for row in rows(context, DocumentRecord)} == before_docs
    assert {row.id for row in rows(context, DocumentChunkRecord)} == before_chunks
    assert set(collection.get()["ids"]) == before_vectors
    chosen = next(item for item in items.items if item.title == "SelectedQuasar")
    response = await context.harness.client.post(
        f"/api/import-batches/{batch['id']}/confirm",
        json={
            "discoveryVersion": batch["discoveryVersion"],
            "items": [{"itemId": str(chosen.id), "decision": "create"}],
        },
    )
    assert response.status_code == 200, response.text
    final = await context.harness.wait_for_batch(batch["id"])
    assert final.state == "completed"
    jobs = [row for row in rows(context, ImportJobRecord) if row.id not in before_jobs]
    documents = [row for row in rows(context, DocumentRecord) if row.id not in before_docs]
    assert len(jobs) == len(documents) == 1
    assert jobs[0].state == "completed"
    final_items = await context.harness.all_batch_items(batch["id"])
    assert sum(item.import_job_id is not None for item in final_items.items) == 1
    assert next(item for item in final_items.items if item.id != chosen.id).state == "skipped"
    chunks = [
        row for row in rows(context, DocumentChunkRecord) if row.document_id == documents[0].id
    ]
    assert chunks and all("SelectedQuasar" in chunk.text for chunk in chunks)
    collection = context.state.vector_store.client.get_collection(
        context.state.vector_store.collection_name(context.repository.id)
    )
    indexed = collection.get(include=["documents"])
    assert {chunk.id for chunk in chunks}.issubset(set(indexed["ids"]))
    assert "SelectedQuasar" in "\n".join(indexed["documents"])
    assert "SkippedNebula" not in "\n".join(indexed["documents"])
    assert "UnpickedPulsar" not in "\n".join(indexed["documents"])
    assert len(context.wire.calls) == 1
    assert_no_secrets(context, caplog)


async def test_key_namespaces_and_provider_failure_are_private(search_app, caplog):
    context = search_app
    client = context.harness.client
    response = await client.put(
        "/api/settings/model",
        json={
            "preset": "custom",
            "baseUrl": "https://model.fixture/v1",
            "model": "fixture-model",
            "timeoutSeconds": 30,
            "apiKey": MODEL_KEY,
        },
    )
    assert response.status_code == 200, response.text
    context.wire.public.append(response.text)
    await settings(context, "auto", "")
    await stream(context)
    assert context.wire.calls == []
    view = await settings(context, "auto")
    assert view["hasApiKey"] and view["webSearch"]["hasApiKey"]
    secret_store = context.state.settings_service.secret_store
    assert secret_store.get("model-api-key") == MODEL_KEY
    search_names = [name for name in secret_store._values if name.startswith("web-search")]
    assert len(search_names) == 1
    assert secret_store.get(search_names[0]) == SEARCH_KEY
    response = await client.post("/api/settings/model/test")
    assert response.status_code == 200
    assert context.wire.model_keys == [MODEL_KEY]
    context.wire.public.append(response.text)
    events = await stream(context)
    assert events[-1]["type"] == "done"
    assert [call["api_key"] for call in context.wire.calls] == [SEARCH_KEY]
    context.wire.fail = True
    events = await stream(context)
    assert events[-1]["type"] == "done", events[-1]
    assert events[-1]["payload"]["warning"]["code"] == "SEARCH_PROVIDER_ERROR"
    events = await stream(context, permission="explicit")
    assert events[-1]["type"] == "error"
    assert events[-1]["payload"]["code"] == "SEARCH_PROVIDER_ERROR"
    failed = next(row for row in rows(context, WebSearchRunRecord) if row.status == "failed")
    assert failed.error_code == "SEARCH_PROVIDER_ERROR"
    for path in (f"/api/search/runs/{failed.id}", f"/api/web-search/runs/{failed.id}"):
        response = await client.get(path, params={"session_id": context.session.id})
        assert response.status_code == 200, response.text
        assert response.json()["results"] == []
        context.wire.public.append(response.text)
    response = await client.post("/api/settings/web-search/test")
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is False
    response = await client.put(
        "/api/settings/model",
        json={
            "preset": "custom",
            "baseUrl": "https://model.fixture/v1",
            "model": "fixture-model",
            "timeoutSeconds": 30,
            "apiKey": "",
        },
    )
    assert response.status_code == 200, response.text
    assert secret_store.get("model-api-key") is None
    assert secret_store.get(search_names[0]) == SEARCH_KEY
    response = await client.put(
        "/api/settings/model",
        json={
            "preset": "custom",
            "baseUrl": "https://model.fixture/v1",
            "model": "fixture-model",
            "timeoutSeconds": 30,
            "apiKey": MODEL_KEY,
        },
    )
    assert response.status_code == 200, response.text
    await settings(context, "off", "")
    assert secret_store.get("model-api-key") == MODEL_KEY
    assert secret_store.get(search_names[0]) is None
    response = await client.get("/api/settings")
    context.wire.public.append(response.text)
    assert response.json()["hasApiKey"] is True
    assert response.json()["webSearch"]["hasApiKey"] is False
    assert_no_secrets(context, caplog)
