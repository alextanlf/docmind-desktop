from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.config import AppSettings
from app.storage.models import (
    BatchImportRecord,
    BatchItemRecord,
    BatchItemState,
    BatchState,
    ImportJobRecord,
    ImportStatus,
)

from .conftest import RUNTIME_TOKEN


@pytest.mark.asyncio
async def test_restart_pauses_running_parent_and_continues_without_duplicate_child_jobs(test_app):
    repository = await test_app.create_repository("重启边界验收")
    collection_id = test_app.stage_fixture_directory()
    original = await test_app.create_batch(collection_id, repository.id)
    for _ in range(100):
        if test_app.app.state.batch_store.get(str(original.id)).state is BatchState.AWAITING_CONFIRMATION:
            break
        await asyncio.sleep(0.01)
    page = await test_app.all_batch_items(str(original.id))
    await test_app.confirm_batch(
        str(original.id), original.discovery_version,
        [{"itemId": str(item.id), "decision": "create"} for item in page.items],
    )
    await test_app.wait_for_batch(str(original.id))
    completed_item = test_app.app.state.batch_store.list_item_records(str(original.id))[0]

    parent = BatchImportRecord(
        id=str(uuid4()),
        source_kind="staged_directory",
        source_descriptor_json=json.dumps({"collectionId": collection_id}),
        repository_id=repository.id,
        state=BatchState.RUNNING,
        total_count=1,
        selected_count=1,
        message="批次导入中",
    )
    test_app.app.state.batch_store.create_batch(parent)
    completed_job = test_app.app.state.import_job_store.get(completed_item.import_job_id)
    assert completed_job is not None
    synthetic_job = ImportJobRecord(
        id=str(uuid4()),
        source_kind=completed_job.source_kind,
        source_value=completed_job.source_value,
        repository_id=completed_job.repository_id,
        state=ImportStatus.COMPLETED,
        current_stage=ImportStatus.COMPLETED.value,
        progress=100,
        message="导入完成",
        document_id=completed_job.document_id,
    )
    test_app.app.state.import_job_store.create(synthetic_job)
    completed_job_count = len(test_app.app.state.import_job_store.list())
    remote_document_count = len(test_app.app.state.yuque_gateway._documents)  # type: ignore[attr-defined]
    synthetic = BatchItemRecord(
        batch_id=parent.id,
        source_identity=completed_item.source_identity,
        source_revision=completed_item.source_revision,
        title=completed_item.title,
        display_path=completed_item.display_path,
        media_type=completed_item.media_type,
        size_bytes=completed_item.size_bytes,
        cached_source_json=completed_item.cached_source_json,
        allowed_actions_json='["create","skip"]',
        selected=True,
        decision="create",
    )
    test_app.app.state.batch_store.insert_discovered_items(parent.id, [synthetic])
    with test_app.app.state.database.session() as session:
        record = session.get(BatchItemRecord, synthetic.id)
        record.state = BatchItemState.COMPLETED
        record.import_job_id = synthetic_job.id

    settings = AppSettings(session_token=SecretStr(RUNTIME_TOKEN), data_dir=test_app.app.state.settings.data_dir, environment="test")
    from app.main import create_app

    restarted = create_app(settings, yuque_gateway=test_app.app.state.yuque_gateway)
    async with restarted.router.lifespan_context(restarted), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=restarted), base_url="http://docmind.test", headers={"X-DocMind-Token": RUNTIME_TOKEN}
    ) as client:
        harness = test_app.__class__(client, restarted)
        paused = (await client.get(f"/api/import-batches/{parent.id}")).json()
        assert paused["state"] == "paused"
        continued = await client.post(f"/api/import-batches/{parent.id}/continue")
        continued.raise_for_status()
        final = await harness.wait_for_batch(parent.id)
        assert final.state == "completed"
        assert len(restarted.state.import_job_store.list()) == completed_job_count
        assert len(restarted.state.yuque_gateway._documents) == remote_document_count  # type: ignore[attr-defined]
        assert restarted.state.batch_store.list_item_records(parent.id)[0].state is BatchItemState.COMPLETED
