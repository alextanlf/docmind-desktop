from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from pydantic import SecretStr

from app.config import AppSettings
from app.imports.events import EventEnvelope
from app.schemas.batches import BatchImportView, BatchItemPage
from app.schemas.imports import ImportJobView, SourcePreview
from app.schemas.repositories import RepositoryView
from app.schemas.sessions import SessionSummary
from app.yuque.gateway import FakeYuqueGateway

RUNTIME_TOKEN = "integration-runtime-token"


class TestAppHarness:
    __test__ = False

    def __init__(self, client: httpx.AsyncClient, app) -> None:  # type: ignore[no-untyped-def]
        self.client = client
        self.app = app

    def stage_fixture_directory(self, name: str = "directory-fixture") -> str:
        """Write a deterministic three-file collection snapshot for batch tests."""
        collection_id = str(uuid4())
        collection_root = self.app.state.settings.staging_dir / "collections" / collection_id
        items_root = collection_root / "items"
        items_root.mkdir(parents=True, exist_ok=True)
        entries: list[dict[str, object]] = []
        documents = (
            ("guide.md", "# Guide\n\n@State 管理视图拥有的状态。"),
            ("architecture.md", "# Architecture\n\n目录批量导入保持来源引用。"),
            ("notes.md", "# Notes\n\n恢复后继续不会重复远端写入。"),
        )
        for ordinal, (relative_path, content) in enumerate(documents):
            staged_id = str(uuid4())
            payload = content.encode("utf-8")
            (items_root / staged_id).write_bytes(payload)
            entries.append(
                {
                    "relativePath": relative_path,
                    "stagedId": staged_id,
                    "mediaType": "text/markdown",
                    "sizeBytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "ordinal": ordinal,
                }
            )
        (collection_root / "manifest.json").write_text(
            json.dumps({"rootId": name, "files": entries}), encoding="utf-8"
        )
        return collection_id

    async def create_batch(self, collection_id: str, repository_id: str) -> BatchImportView:
        response = await self.client.post(
            "/api/import-batches",
            json={"kind": "staged_directory", "sourceId": collection_id, "repositoryId": repository_id},
        )
        response.raise_for_status()
        return BatchImportView.model_validate(response.json())

    async def all_batch_items(self, batch_id: str) -> BatchItemPage:
        response = await self.client.get(f"/api/import-batches/{batch_id}/items", params={"limit": 1000})
        response.raise_for_status()
        return BatchItemPage.model_validate(response.json())

    async def confirm_batch(self, batch_id: str, discovery_version: int, decisions: list[dict[str, str]]) -> BatchImportView:
        response = await self.client.post(
            f"/api/import-batches/{batch_id}/confirm",
            json={"discoveryVersion": discovery_version, "items": decisions},
        )
        response.raise_for_status()
        return BatchImportView.model_validate(response.json())

    async def wait_for_batch(self, batch_id: str) -> BatchImportView:
        final: dict[str, object] = {}
        for _ in range(500):
            response = await self.client.get(f"/api/import-batches/{batch_id}")
            response.raise_for_status()
            final = response.json()
            batch = BatchImportView.model_validate(final)
            if batch.state in {"completed", "completed_with_errors", "failed", "cancelled"}:
                return batch
            await asyncio.sleep(0.01)
        pytest.fail(f"batch timed out: {final}")

    async def create_repository(self, name: str) -> RepositoryView:
        response = await self.client.post("/api/repositories", json={"name": name})
        response.raise_for_status()
        return RepositoryView.model_validate(response.json())

    async def inspect_staged(self, staged_id: str) -> SourcePreview:
        response = await self.client.post(
            "/api/imports/inspect", json={"kind": "staged_file", "value": staged_id}
        )
        response.raise_for_status()
        return SourcePreview.model_validate(response.json())

    async def create_import(self, preview: SourcePreview, repository_id: str) -> ImportJobView:
        response = await self.client.post(
            "/api/imports",
            json={
                "source": {"kind": preview.source_kind, "value": _staged_id(preview.source_url)},
                "repositoryId": repository_id,
                "fingerprint": preview.fingerprint,
            },
        )
        response.raise_for_status()
        return ImportJobView.model_validate(response.json())

    async def wait_for_import(self, job_id: str) -> ImportJobView:
        final: dict[str, object] = {}
        for _ in range(500):
            response = await self.client.get(f"/api/imports/{job_id}")
            response.raise_for_status()
            final = response.json()
            job = ImportJobView.model_validate(final)
            if job.state in {"completed", "failed", "cancelled"}:
                if job.state != "completed":
                    pytest.fail(f"import did not complete: {final}")
                return job
            await asyncio.sleep(0.01)
        pytest.fail(f"import timed out: {final}")

    async def create_session(self, repository_ids: list[str]) -> SessionSummary:
        response = await self.client.post("/api/sessions", json={"repositoryIds": repository_ids})
        response.raise_for_status()
        return SessionSummary.model_validate(response.json())

    async def ask(self, session_id: str, message: str) -> list[EventEnvelope]:
        response = await self.client.post(
            f"/api/sessions/{session_id}/messages/stream",
            json={
                "requestId": str(uuid4()),
                "message": message,
                "repositoryIds": (await self._session_repositories(session_id)),
            },
        )
        response.raise_for_status()
        events = [
            EventEnvelope.model_validate(json.loads(line.removeprefix("data: ")))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert events
        return events

    async def _session_repositories(self, session_id: str) -> list[str]:
        response = await self.client.get("/api/sessions")
        response.raise_for_status()
        session = next(
            SessionSummary.model_validate(item)
            for item in response.json()
            if item["id"] == session_id
        )
        return session.repository_ids


def _staged_id(source_url: str | None) -> str:
    assert source_url is not None and source_url.startswith("staged://")
    return source_url.removeprefix("staged://")


@pytest_asyncio.fixture
async def test_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> AsyncIterator[TestAppHarness]:
    monkeypatch.setenv("DOCMIND_FAKE_SERVICES", "1")
    settings = AppSettings(
        session_token=SecretStr(RUNTIME_TOKEN), data_dir=tmp_path / "docmind-data", environment="test"
    )
    from app.main import create_app

    app = create_app(settings, yuque_gateway=FakeYuqueGateway())
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=transport,
        base_url="http://docmind.test",
        headers={"X-DocMind-Token": RUNTIME_TOKEN},
    ) as client:
        yield TestAppHarness(client, app)


@pytest.fixture
def staged_markdown(test_app: TestAppHarness) -> str:
    staged_id = str(uuid4())
    staged_path = test_app.client._transport.app.state.settings.staging_dir / f"{staged_id}.md"  # type: ignore[attr-defined]
    staged_path.write_text("# 状态管理\n## @State\n@State 管理视图拥有的状态。", encoding="utf-8")
    return staged_id
