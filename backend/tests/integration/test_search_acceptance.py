from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from app.core.llm import ChatDelta, LLMToolCallDelta, ModelConnectionResult
from app.search.tavily import TAVILY_SECRET_NAME, TavilyProvider
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
    """充当"会调用工具的模型"。

    联网现在由模型发起而非相似度阈值，所以只吐文本的桩永远不会触发搜索：
    - 请求带了工具、且提示词里还没有网页证据 → 先要求一次 `web_search`；
    - 拿到配对的 `role=tool` 结果、或已经有预检索塞进来的 `<web-context>` → 直接作答。
    """

    def __init__(self):
        self.requests = []

    async def stream_chat(self, request):
        self.requests.append(request)
        answered = any(message.role == "tool" for message in request.messages)
        has_web_evidence = any("<web-context>" in message.content for message in request.messages)
        if request.tools and not answered and not has_web_evidence:
            yield ChatDelta(
                tool_calls=[
                    LLMToolCallDelta(
                        index=0,
                        id="call-search",
                        name="web_search",
                        arguments=json.dumps({"query": QUESTION}, ensure_ascii=False),
                    )
                ]
            )
            return
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
    wire = SimpleNamespace(calls=[], headers=[], fail=False, model_keys=[], public=[])

    async def record_public_response(response):
        await response.aread()
        wire.public.append(response.text)

    test_app.client.event_hooks["response"].append(record_public_response)

    def respond(request):
        # 搜索链现在只剩两级：模型内置联网（custom 预设下不可用）→ Tavily。
        assert str(request.url) == "https://tavily.fixture/search"
        assert request.method == "POST"
        payload = json.loads(request.content)
        wire.calls.append(payload)
        # 免密钥模式全靠这个头。不记录它的话，"没有 api_key" 这个断言对着一个
        # 连头都没发的实现也会通过。
        wire.headers.append(request.headers.get("x-tavily-access-mode"))
        if wire.fail:
            raise httpx.ConnectError("provider failed", request=request)
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

        def provider_factory(api_key=None):
            return TavilyProvider(api_key, client=provider_client)

        monkeypatch.setattr("app.main.TavilyProvider", provider_factory)
        # Query planning is covered by unit tests; keep the integration flow
        # offline and deterministic unless a test installs its own planner.
        state.search_service.query_planner = None
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


def configure_search_key(context, key=SEARCH_KEY):
    """直接写密钥。

    设置页的「联网搜索」分区已删除，应用内不再有任何写入入口 —— 这里模拟"以前
    配过 Key 的安装"，验证那条路径仍然生效。
    """
    context.state.settings_service.secret_store.set(TAVILY_SECRET_NAME, key)


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


def citations_from(events):
    """全部 citations 事件里的引用合集。

    联网改由模型发起之后，首个 citations 事件（发在生成之前）不可能包含工具轮注册
    的网页引用 —— 它们由生成结束后的补发事件带出来，所以断言必须跨事件聚合。
    """
    return [
        item
        for event in events
        if event["type"] == "citations"
        for item in event["payload"]["citations"]
    ]


@pytest.fixture
def captured_logs():
    """自己收集日志，**不用 `caplog`**。

    🔴 这里踩过一个会制造假绿的坑：本套件在该位置 `caplog` 收不到任何记录 —— 实测在函数里
    `logging.getLogger("probe").warning("…")` 之后 `caplog.text` 仍为空，而**同样写法在
    `tests/core` 里正常**。机制是全局 logging 状态被别的测试改动（`root.manager.disable`
    非 NOTSET 时 `isEnabledFor` 直接返回 False，记录根本不产生）。
    后果是下面的"密钥不得出现在日志里"会**永远通过**，等于没测。

    所以这里自己挂 handler 覆盖整段测试，并在期间把 disable 归零；配合 canary 自证通道是活的。
    """
    records: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    root = logging.getLogger()
    capture = _Capture()
    previous_disable = logging.root.manager.disable
    logging.disable(logging.NOTSET)
    root.addHandler(capture)
    try:
        yield records
    finally:
        root.removeHandler(capture)
        logging.disable(previous_disable)


def assert_no_secrets(context, logs) -> None:
    # 🔴 canary 自证：通道没抓到记录就直接失败，否则"日志里没有密钥"会退化成永远通过。
    canary = "docmind-log-canary"
    logging.getLogger("probe").warning(canary)
    assert canary in logs, (
        "日志通道没抓到记录，下面的泄漏断言会是空转 :: "
        f"probe.disabled={logging.getLogger('probe').disabled} "
        f"root.level={logging.getLogger().level} "
        f"manager.disable={logging.root.manager.disable}"
    )
    with context.state.database.engine.connect() as connection:
        dump = "\n".join(connection.connection.driver_connection.iterdump())
    public = "\n".join(context.wire.public) + "\n".join(logs) + dump
    for secret in (MODEL_KEY, SEARCH_KEY):
        assert secret not in public


async def test_permission_off_makes_zero_provider_calls(search_app):
    """唯一的联网开关是 per-request 权限：off 时连工具都不下发。"""
    context = search_app
    events = await stream(context, permission="off")
    assert events[-1]["type"] == "done", events[-1]
    # 没有本地证据、又不许联网 → 直接认输，并给出「联网搜索」入口。
    assert events[-1]["payload"]["searchSuggested"] is True
    assert events[-1]["payload"]["userMessageId"]
    assert context.wire.calls == []
    assert context.llm.requests == []
    assert rows(context, WebSearchRunRecord) == []


async def test_tavily_keyless_runs_without_any_missing_key(search_app):
    """没有任何 Key 也能搜 —— 免密钥档是设置页那一栏能被删掉的前提。"""
    context = search_app
    events = await stream(context)
    assert events[-1]["type"] == "done", events[-1]
    assert len(context.wire.calls) == 1
    # 🔴 免密钥的关键证据：不带 api_key，且必须真的发出免密钥请求头。
    assert "api_key" not in context.wire.calls[0]
    assert context.wire.headers == ["keyless"]
    runs = rows(context, WebSearchRunRecord)
    assert len(runs) == 1
    assert runs[0].provider == "tavily"
    assert runs[0].status == "completed"
    citations = citations_from(events)
    assert any(item.get("sourceUrl") == "https://evidence.test/SelectedQuasar" for item in citations)


class StubPlanner:
    def __init__(self, variants: list[str]) -> None:
        self.variants = variants
        self.calls = 0

    async def plan(self, query: str, *, max_queries: int = 3) -> list[str]:
        self.calls += 1
        return [query, *self.variants][:max_queries]


async def test_query_rewrite_searches_every_variant(search_app):
    context = search_app
    planner = StubPlanner(["DeepSeek V4 发布时间", "DeepSeek V4 release date"])
    context.state.search_service.query_planner = planner

    events = await stream(context)

    assert events[-1]["type"] == "done", events[-1]
    assert planner.calls == 1
    assert len(context.wire.calls) == 3
    assert {call["query"] for call in context.wire.calls} == {
        QUESTION,
        "DeepSeek V4 发布时间",
        "DeepSeek V4 release date",
    }
    runs = rows(context, WebSearchRunRecord)
    assert len(runs) == 1
    assert runs[0].status == "completed"
    assert runs[0].provider == "tavily"


async def explicit_results(context):
    # 第一回合不联网：这正是用户看到"文档未覆盖 + 联网搜索按钮"的场景。
    initial = await stream(context, permission="off")
    assert initial[-1]["payload"]["searchSuggested"] is True
    original_id = initial[-1]["payload"]["userMessageId"]
    # 续搜端点强制 explicit 授权（用户已经点了按钮）→ 预检索一次。
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


async def test_explicit_continuation_persists_only_registered_w_citations(
    search_app, captured_logs
):
    await explicit_results(search_app)
    assert_no_secrets(search_app, captured_logs)


async def test_explicit_w_citation_then_partial_second_confirmation(search_app, captured_logs):
    context = search_app
    before_jobs = {row.id for row in rows(context, ImportJobRecord)}
    before_docs = {row.id for row in rows(context, DocumentRecord)}
    before_chunks = {row.id for row in rows(context, DocumentChunkRecord)}
    before_vectors = {
        entry["id"] for entry in context.state.vector_store.list_stored(context.repository.id)
    }
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
    assert {
        entry["id"] for entry in context.state.vector_store.list_stored(context.repository.id)
    } == before_vectors
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
    indexed = context.state.vector_store.list_stored(context.repository.id)
    assert {chunk.id for chunk in chunks}.issubset({entry["id"] for entry in indexed})
    assert "SelectedQuasar" in "\n".join(entry["text"] for entry in indexed)
    assert "SkippedNebula" not in "\n".join(entry["text"] for entry in indexed)
    assert "UnpickedPulsar" not in "\n".join(entry["text"] for entry in indexed)
    assert len(context.wire.calls) == 1
    assert_no_secrets(context, captured_logs)


async def test_legacy_search_key_is_used_but_never_leaked(search_app, captured_logs):
    """设置页删除后 Key 只读：仍需生效（付费档不限流），且不能出现在任何公开输出里。"""
    context = search_app
    client = context.harness.client
    configure_search_key(context)

    events = await stream(context)
    assert events[-1]["type"] == "done", events[-1]
    assert [call.get("api_key") for call in context.wire.calls] == [SEARCH_KEY]
    # 有 Key 就不该再声明免密钥档 —— 两条路径不能同时生效。
    assert "keyless" not in context.wire.headers

    # 模型发起（auto）时联网失败是降级提示，不把整条回答变成错误。
    context.wire.fail = True
    events = await stream(context)
    assert events[-1]["type"] == "done", events[-1]
    assert events[-1]["payload"]["warning"]["code"] == "SEARCH_PROVIDER_ERROR"
    # 🔴 用户显式点了「联网搜索」时**同样降级**：联网只是补充证据，把它当主路径等于用
    # "外部搜索挂了"去否掉本地文档本来能答的问题。但必须讲出来，不能静默。
    before = len(context.llm.requests)
    events = await stream(context, permission="explicit")
    assert events[-1]["type"] == "done", events[-1]
    assert events[-1]["payload"]["warning"]["code"] == "SEARCH_PROVIDER_ERROR"
    # 降级 ≠ 跳过生成：失败之后模型仍然必须被叫起来基于已有资料作答。
    assert len(context.llm.requests) > before
    failed = next(row for row in rows(context, WebSearchRunRecord) if row.status == "failed")
    assert failed.error_code == "SEARCH_PROVIDER_ERROR"
    for path in (f"/api/search/runs/{failed.id}", f"/api/web-search/runs/{failed.id}"):
        response = await client.get(path, params={"session_id": context.session.id})
        assert response.status_code == 200, response.text
        assert response.json()["results"] == []
        context.wire.public.append(response.text)

    # 设置视图里不该再有任何联网分区（这是本次删除的对象）。
    response = await client.get("/api/settings")
    context.wire.public.append(response.text)
    assert response.status_code == 200, response.text
    assert "webSearch" not in response.json()
    # 删除端点本身：旧客户端调用应当拿到 404/405，而不是静默成功。
    response = await client.put("/api/settings/web-search", json={"mode": "auto"})
    assert response.status_code in (404, 405)
    assert_no_secrets(context, captured_logs)
