from __future__ import annotations

import asyncio
import json

import pytest

from app.storage.models import BatchItemState


@pytest.mark.asyncio
async def test_directory_snapshot_confirms_three_imports_and_preserves_citations(test_app):
    repository = await test_app.create_repository("目录批量验收")
    collection_id = test_app.stage_fixture_directory()

    batch = await test_app.create_batch(collection_id, repository.id)
    for _ in range(100):
        snapshot = test_app.app.state.batch_store.get(str(batch.id))
        if snapshot is not None and snapshot.state.value == "awaiting_confirmation":
            break
        await asyncio.sleep(0.01)
    items_page = await test_app.all_batch_items(str(batch.id))
    assert len(items_page.items) == 3
    discovered = test_app.app.state.batch_store.list_item_records(str(batch.id))
    assert {item.source_identity for item in discovered} == {
        "folder:directory-fixture:guide.md",
        "folder:directory-fixture:architecture.md",
        "folder:directory-fixture:notes.md",
    }
    cached_paths = [json.loads(item.cached_source_json) for item in discovered]
    assert all("/" not in ref["cacheId"] for ref in cached_paths)

    decisions = [{"itemId": str(item.id), "decision": "create"} for item in items_page.items]
    await test_app.confirm_batch(str(batch.id), batch.discovery_version, decisions)
    terminal = await test_app.wait_for_batch(str(batch.id))
    assert terminal.state == "completed"
    assert terminal.completed_count == 3
    assert terminal.selected_count == 3

    jobs = test_app.app.state.import_job_store.list()
    assert len(jobs) == 3
    assert all(job.state.value == "completed" for job in jobs)
    for item in test_app.app.state.batch_store.list_item_records(str(batch.id)):
        assert item.state is BatchItemState.COMPLETED
        metadata = json.loads(test_app.app.state.import_job_store.get(item.import_job_id).source_value)  # type: ignore[arg-type]
        assert "/" not in metadata["collection_cache"]["cache_ref"]["cacheId"]

    second_continue = await test_app.client.post(f"/api/import-batches/{batch.id}/continue")
    second_continue.raise_for_status()
    assert len(test_app.app.state.import_job_store.list()) == 3

    session = await test_app.create_session([repository.id])
    events = await test_app.ask(session.id, "@State 有什么作用？")
    citation_events = [event for event in events if event.type == "citations"]
    assert citation_events
    citations = citation_events[0].payload.get("citations", [])
    assert citations and citations[0]["sourceId"] == "S1"
