from __future__ import annotations

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import AppSettings, EmbeddingSettings
from app.core.embedding import FakeEmbeddingProvider
from app.core.secrets import MemorySecretStore
from app.main import create_app


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
