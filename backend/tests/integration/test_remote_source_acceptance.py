from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select

from app.document.safe_http import SafeHttpClient
from app.main import create_app
from app.schemas.batches import DiscoveryRequest
from app.schemas.yuque import CreateYuqueDocumentRequest
from app.storage.models import CrawlEntryRecord, CrawlEntryState


class PublicResolver:
    async def resolve(self, host):
        assert host == "site.test"
        return ["93.184.216.34"]


@pytest.fixture(autouse=True)
def forbid_real_http(monkeypatch):
    async def reject(*args, **kwargs):
        pytest.fail("Real HTTP transport is forbidden in remote-source acceptance")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", reject)


async def confirm_selected(harness, batch, selected, decision):
    response = await harness.client.post(f"/api/import-batches/{batch['id']}/confirm", json={
        "discoveryVersion": batch["discoveryVersion"],
        "items": [{"itemId": str(selected.id), "decision": decision}]
    })
    assert response.status_code == 200, response.text


async def discover(harness, payload):
    response = await harness.client.post("/api/import-batches", json=payload)
    response.raise_for_status()
    batch_id = response.json()["id"]
    for _ in range(500):
        response = await harness.client.get(f"/api/import-batches/{batch_id}")
        response.raise_for_status()
        batch = response.json()
        if batch["state"] != "discovering":
            assert batch["state"] == "awaiting_confirmation", json.dumps(batch, ensure_ascii=False)
            return batch
        await asyncio.sleep(0.01)
    pytest.fail(f"discovery timed out: {batch}")


def frontier_rows(app, batch_id):
    with app.state.database.session() as session:
        return list(session.scalars(select(CrawlEntryRecord).where(
            CrawlEntryRecord.batch_id == batch_id
        )))


def assert_snapshots(app, batch_id):
    for item in app.state.batch_store.list_item_records(batch_id):
        cache = json.loads(item.cached_source_json)
        path = app.state.settings.staging_dir / "remote" / batch_id / cache["cacheId"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == cache["sha256"]


async def assert_index_and_citations(harness, repository_id, expected_ids):
    documents = harness.app.state.document_store.list_for_repository(repository_id)
    assert {document.id for document in documents} == expected_ids
    for document in documents:
        assert Path(document.markdown_path).is_file()
        chunks = harness.app.state.document_store.list_chunks(document.id)
        assert len(chunks) == document.chunk_count > 0
    vectors = harness.app.state.vector_store
    collection = vectors.client.get_collection(vectors.collection_name(repository_id))
    stored = collection.get(include=["metadatas", "documents"])
    assert {metadata["doc_id"] for metadata in stored["metadatas"]} == expected_ids
    assert len(stored["ids"]) == sum(document.chunk_count for document in documents)
    assert set(stored["ids"]) == {
        vector_id for document in documents
        for vector_id in harness.app.state.document_store.vector_ids(document.id)
    }
    session = await harness.create_session([repository_id])
    events = await harness.ask(session.id, "@State 有什么作用？")
    citations = [citation for event in events if event.type == "citations"
                 for citation in event.payload["citations"]]
    assert citations
    assert {citation["documentId"] for citation in citations} <= expected_ids
    assert all(citation["sourceId"].startswith("S") and citation["excerpt"] for citation in citations)


@pytest.mark.asyncio
async def test_web_recursive_sitemap_partial_confirm_indexes_only_selected(test_app):
    repository = await test_app.create_repository("网站验收")
    requested = []
    routes = {
        "/robots.txt": ("text/plain", "User-agent: *\nAllow: /"),
        "/sitemap.xml": ("application/xml", ('<urlset><url><loc>https://site.test/docs/map</loc></url>'
                         '<url><loc>https://other.test/docs/escape</loc></url>'
                         '<url><loc>https://site.test/docs-other/escape</loc></url></urlset>')),
        "/docs/": ("text/html", ('<title>入口</title><a href="child">child</a>'
                   '<a href="/docs-other/escape">escape</a><a href="https://other.test/docs/">escape</a>')),
        "/docs/child": ("text/html", ('<title>状态</title><p>@State 管理视图拥有的状态。</p>'
                        '<a href="/docs/">cycle</a><a href="deep">deep</a>')),
        "/docs/deep": ("text/html", "<title>深层</title><p>递归发现。</p>"),
        "/docs/map": ("text/html", "<title>地图</title><p>仅由 sitemap 发现。</p>"),
    }

    def respond(request):
        assert request.url.host == "site.test"
        requested.append(request.url.path)
        assert request.url.path in routes
        media, body = routes[request.url.path]
        return httpx.Response(200, headers={"content-type": media}, text=body)

    test_app.app.state.batch_service.web_discovery.client = SafeHttpClient(
        resolver=PublicResolver(), transport=httpx.MockTransport(respond)
    )
    batch = await discover(test_app, {
        "kind": "web", "entryUrl": "https://site.test/docs/", "repositoryId": repository.id
    })
    batch_id = batch["id"]
    page = await test_app.all_batch_items(batch_id)
    assert {item.display_path for item in page.items} == {
        "https://site.test/docs/", "https://site.test/docs/child",
        "https://site.test/docs/deep", "https://site.test/docs/map"
    }
    assert test_app.app.state.document_store.list_for_repository(repository.id) == []
    assert test_app.app.state.import_job_store.list() == []
    assert_snapshots(test_app.app, batch_id)
    rows = frontier_rows(test_app.app, batch_id)
    assert {row.canonical_url for row in rows} == {item.display_path for item in page.items}
    assert all(row.state == CrawlEntryState.FETCHED and row.fetched_at for row in rows)
    assert requested.count("/docs/") == 1
    selected = next(item for item in page.items if item.display_path.endswith("/child"))
    await confirm_selected(test_app, batch, selected, "create")
    terminal = await test_app.wait_for_batch(batch_id)
    assert terminal.state == "completed"
    assert terminal.selected_count == terminal.completed_count == 1
    jobs = test_app.app.state.import_job_store.list()
    assert len(jobs) == 1
    items = test_app.app.state.batch_store.list_item_records(batch_id)
    assert all(item.import_job_id is None for item in items if item.id != str(selected.id))
    await assert_index_and_citations(test_app, repository.id, {jobs[0].document_id})


@pytest.mark.asyncio
async def test_web_200_page_limit_preserves_pending_frontier_on_restart(test_app):
    repository = await test_app.create_repository("frontier边界")
    requested = []

    def respond(request):
        requested.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        body = "<title>页面</title><p>bounded crawl</p>"
        if request.url.path == "/docs/":
            body += "".join(f'<a href="/docs/{index}">page</a>' for index in range(205))
        return httpx.Response(200, headers={"content-type": "text/html"}, text=body)

    test_app.app.state.batch_service.web_discovery.client = SafeHttpClient(
        resolver=PublicResolver(), transport=httpx.MockTransport(respond)
    )
    batch = await discover(test_app, {
        "kind": "web", "entryUrl": "https://site.test/docs/", "repositoryId": repository.id,
        "useSitemap": False
    })
    rows = frontier_rows(test_app.app, batch["id"])
    assert sum(row.state == CrawlEntryState.FETCHED for row in rows) == 200
    assert sum(row.state == CrawlEntryState.PENDING for row in rows) == 6
    assert len(requested) == 201
    assert len((await test_app.all_batch_items(batch["id"])).items) == 200
    assert test_app.app.state.import_job_store.list() == []
    assert test_app.app.state.document_store.list_for_repository(repository.id) == []
    restarted = create_app(test_app.app.state.settings, yuque_gateway=test_app.app.state.yuque_gateway)
    async with restarted.router.lifespan_context(restarted):
        assert {(row.id, row.state) for row in frontier_rows(restarted, batch["id"])} == {
            (row.id, row.state) for row in rows
        }
        assert restarted.state.document_store.list_for_repository(repository.id) == []


@pytest.mark.asyncio
async def test_yuque_manual_pull_attach_indexes_and_restart_never_writes(test_app):
    repository = await test_app.create_repository("语雀只读验收")
    gateway = test_app.app.state.yuque_gateway
    local = test_app.app.state.repository_store.get(repository.id)
    remote = await gateway.create_document(CreateYuqueDocumentRequest(
        repository_id=local.yuque_id, title="状态指南",
        content="# 状态指南\n\n@State 管理视图拥有的状态。\n<!-- docmind fixture -->"
    ))
    unselected_remote = await gateway.create_document(CreateYuqueDocumentRequest(
        repository_id=local.yuque_id, title="不选择", content="# 不选择\n\n不得导入。"
    ))
    gateway.write_calls.clear()
    gateway.read_calls.clear()
    batch = await discover(test_app, {"kind": "yuque_repository", "repositoryId": repository.id})
    assert gateway.write_calls == []
    assert gateway.read_calls.count("list_documents") == 1
    assert gateway.read_calls.count("read_document") == 2
    assert test_app.app.state.document_store.list_for_repository(repository.id) == []
    assert test_app.app.state.import_job_store.list() == []
    assert_snapshots(test_app.app, batch["id"])
    records = test_app.app.state.batch_store.list_item_records(batch["id"])
    assert all(json.loads(item.remote_binding_json)["repositoryId"] == local.yuque_id
               for item in records)
    page = await test_app.all_batch_items(batch["id"])
    assert len(page.items) == 2
    selected = next(item for item in page.items if item.title == "状态指南")
    assert "attach_remote" in selected.allowed_actions
    await confirm_selected(test_app, batch, selected, "attach_remote")
    terminal = await test_app.wait_for_batch(batch["id"])
    assert terminal.state == "completed"
    assert terminal.completed_count == terminal.selected_count == 1
    jobs = test_app.app.state.import_job_store.list()
    assert len(jobs) == 1
    assert json.loads(jobs[0].source_value)["attach_remote"] is True
    document = test_app.app.state.document_store.get(jobs[0].document_id)
    assert document.yuque_id == remote.yuque_id
    assert "docmind fixture" not in Path(document.markdown_path).read_text()
    await assert_index_and_citations(test_app, repository.id, {document.id})
    assert gateway.write_calls == []
    restarted = create_app(test_app.app.state.settings, yuque_gateway=gateway)
    async with restarted.router.lifespan_context(restarted), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=restarted), base_url="http://docmind.test",
        headers=dict(test_app.client.headers)
    ) as client:
        harness = test_app.__class__(client, restarted)
        response = await client.post(f"/api/import-batches/{batch['id']}/continue")
        response.raise_for_status()
        assert (await harness.wait_for_batch(batch["id"])).state == "completed"
        synced_documents: list = []
        for _ in range(80):
            synced_documents = restarted.state.document_store.list_for_repository(repository.id)
            if {item.yuque_id for item in synced_documents} == {
                remote.yuque_id,
                unselected_remote.yuque_id,
            } and all(item.markdown_path for item in synced_documents):
                break
            await asyncio.sleep(0.05)
        assert {item.yuque_id for item in synced_documents} == {
            remote.yuque_id,
            unselected_remote.yuque_id,
        }
        await assert_index_and_citations(
            harness, repository.id, {item.id for item in synced_documents}
        )
        assert [job.id for job in restarted.state.import_job_store.list()] == [jobs[0].id]
        assert gateway.write_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["replace", "delete"])
async def test_remote_snapshot_change_rejects_confirm_without_child_or_document(test_app, change):
    repository = await test_app.create_repository("远端快照一致性")

    def respond(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        return httpx.Response(200, headers={"content-type": "text/html"},
                              text="<title>快照</title><p>原始正文</p>")

    test_app.app.state.batch_service.web_discovery.client = SafeHttpClient(
        resolver=PublicResolver(), transport=httpx.MockTransport(respond)
    )
    batch = await discover(test_app, {
        "kind": "web", "entryUrl": "https://site.test/docs/", "repositoryId": repository.id,
        "useSitemap": False
    })
    item = test_app.app.state.batch_store.list_item_records(batch["id"])[0]
    cache = json.loads(item.cached_source_json)
    path = test_app.app.state.settings.staging_dir / "remote" / batch["id"] / cache["cacheId"]
    if change == "replace":
        path.write_bytes(b"x" * cache["byteSize"])
    else:
        path.unlink()
    response = await test_app.client.post(f"/api/import-batches/{batch['id']}/confirm", json={
        "discoveryVersion": batch["discoveryVersion"],
        "items": [{"itemId": item.id, "decision": "create"}]
    })
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "BATCH_STALE_CONFIRMATION"
    assert test_app.app.state.import_job_store.list() == []
    assert test_app.app.state.document_store.list_for_repository(repository.id) == []
    assert test_app.app.state.batch_store.get(batch["id"]).state.value == "awaiting_confirmation"


@pytest.mark.asyncio
async def test_yuque_discovery_resolves_local_repository_binding(test_app):
    repository = await test_app.create_repository("本地到远端绑定")
    gateway = test_app.app.state.yuque_gateway
    local = test_app.app.state.repository_store.get(repository.id)
    remote = await gateway.create_document(CreateYuqueDocumentRequest(
        repository_id=local.yuque_id, title="绑定", content="# 绑定\n\n只读快照"
    ))
    gateway.write_calls.clear()
    async def emit(event):
        return None

    result = await test_app.app.state.batch_service.yuque_discovery.discover(DiscoveryRequest(
        batch_id=uuid4(), source_kind="yuque_repository",
        repository_id=UUID(repository.id), source_descriptor={"repositoryId": repository.id}
    ), emit)
    assert len(result.sources) == 1
    assert result.sources[0].remote_binding.repository_id == local.yuque_id
    assert result.sources[0].remote_binding.document_id == remote.yuque_id
    assert gateway.write_calls == []
