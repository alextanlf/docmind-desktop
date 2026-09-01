from __future__ import annotations

import asyncio
import gc
from types import SimpleNamespace

from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.requests import Request

from app.api.embedding import prepare_embedding
from app.config import AppSettings, EmbeddingSettings
from app.core.embedding import FakeEmbeddingProvider
from app.core.secrets import MemorySecretStore
from app.main import create_app
from app.schemas.embedding import ModelStatus


def test_embedding_routes_require_runtime_token(client: TestClient) -> None:
    assert client.get("/api/embedding/status").status_code == 401
    assert client.post("/api/embedding/prepare").status_code == 401


def test_embedding_prepare_is_explicit(tmp_path) -> None:
    settings = AppSettings(
        session_token=SecretStr("test-runtime-token"), data_dir=tmp_path / "data", environment="test"
    )
    fake_embedding = FakeEmbeddingProvider(EmbeddingSettings(dimension=8))
    with TestClient(
        create_app(settings, secret_store=MemorySecretStore(), embedding_provider=fake_embedding)
    ) as client:
        headers = {"X-DocMind-Token": "test-runtime-token"}
        assert client.get("/api/embedding/status", headers=headers).json()["state"] == "unavailable"
        response = client.post("/api/embedding/prepare", headers=headers)
        assert response.status_code == 202
        assert fake_embedding.ensure_ready_calls == 1


def test_second_prepare_request_reuses_existing_preparation_task(tmp_path) -> None:
    settings = AppSettings(
        session_token=SecretStr("test-runtime-token"), data_dir=tmp_path / "data", environment="test"
    )
    fake_embedding = FakeEmbeddingProvider(EmbeddingSettings(dimension=8), preparation_delay=True)
    with TestClient(
        create_app(settings, secret_store=MemorySecretStore(), embedding_provider=fake_embedding)
    ) as client:
        headers = {"X-DocMind-Token": "test-runtime-token"}
        client.post("/api/embedding/prepare", headers=headers)
        response = client.post("/api/embedding/prepare", headers=headers)
        assert response.status_code == 202
        assert fake_embedding.ensure_ready_calls == 1


def test_completed_prepare_request_returns_current_status_without_restarting(tmp_path) -> None:
    settings = AppSettings(
        session_token=SecretStr("test-runtime-token"), data_dir=tmp_path / "data", environment="test"
    )
    fake_embedding = FakeEmbeddingProvider(EmbeddingSettings(dimension=8))
    with TestClient(
        create_app(settings, secret_store=MemorySecretStore(), embedding_provider=fake_embedding)
    ) as client:
        headers = {"X-DocMind-Token": "test-runtime-token"}
        client.post("/api/embedding/prepare", headers=headers)
        response = client.post("/api/embedding/prepare", headers=headers)

        assert response.status_code == 202
        assert response.json()["state"] == "ready"
        assert fake_embedding.ensure_ready_calls == 1


async def test_failed_background_preparation_consumes_its_terminal_exception() -> None:
    class FailingEmbeddingProvider:
        def __init__(self) -> None:
            self.status = ModelStatus(
                state="unavailable", model_name="test-model", message="模型尚未准备"
            )

        async def ensure_ready(self) -> ModelStatus:
            self.status = ModelStatus(
                state="error", model_name="test-model", message="嵌入模型准备失败"
            )
            raise ValueError("dimension mismatch")

    provider = FailingEmbeddingProvider()
    state = SimpleNamespace(embedding_provider=provider, embedding_prepare_task=None)
    request = Request({"type": "http", "app": SimpleNamespace(state=state)})
    loop = asyncio.get_running_loop()
    contexts: list[dict[str, object]] = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: contexts.append(context))
    try:
        await prepare_embedding(request)
        task = state.embedding_prepare_task
        await asyncio.sleep(0)
        assert task.done()
        assert provider.status.state == "error"

        state.embedding_prepare_task = None
        del task
        gc.collect()
        await asyncio.sleep(0)

        assert not any(
            context.get("message") == "Task exception was never retrieved"
            for context in contexts
        )
    finally:
        loop.set_exception_handler(previous_handler)
