from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import AppSettings
from app.core.secrets import MemorySecretStore
from app.remote.fake import FakeRemoteProvider
from app.storage.database import Database

RUNTIME_TOKEN = "test-runtime-token"


def build_app(settings: AppSettings, *, providers=None, secret_store=None, **kwargs):
    """Create an app for tests, defaulting to an in-memory secret store."""
    from app.main import create_app

    return create_app(
        settings,
        secret_store=secret_store or MemorySecretStore(),
        providers=providers,
        **kwargs,
    )


def install_remote_provider(app, provider=None, *, configured: bool = True):
    """Swap the app's remote registry for one exposing ``provider``.

    API handlers resolve providers through ``app.state.remote_registry`` at
    request time, so a test can install its own provider after startup.
    """
    from app.remote.registry import ProviderRegistry

    provider = provider or FakeRemoteProvider()
    registry = ProviderRegistry()
    if configured:
        registry.register(provider, always_configured=True)
    else:
        registry.register(provider, lambda: False)
    app.state.remote_registry = registry
    return provider


@pytest.fixture
def remote_provider(client: TestClient) -> FakeRemoteProvider:
    provider = client.app.state.remote_registry.get("yuque")
    assert isinstance(provider, FakeRemoteProvider)
    return provider


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[TestClient]:
    monkeypatch.setenv("DOCMIND_SESSION_TOKEN", RUNTIME_TOKEN)
    monkeypatch.setenv("DOCMIND_DATA_DIR", str(tmp_path / "docmind-data"))
    monkeypatch.setenv("DOCMIND_ENVIRONMENT", "test")

    settings = AppSettings(
        session_token=SecretStr(RUNTIME_TOKEN), data_dir=tmp_path / "docmind-data", environment="test"
    )
    with TestClient(
        build_app(settings, providers={"yuque": FakeRemoteProvider()})
    ) as test_client:
        yield test_client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"X-DocMind-Token": RUNTIME_TOKEN}


@pytest.fixture
def app_settings(client: TestClient) -> AppSettings:
    return client.app.state.settings


@pytest.fixture
def app_secret_store(client: TestClient) -> MemorySecretStore:
    return client.app.state.secret_store


@pytest.fixture
def database() -> Iterator[Database]:
    database = Database("sqlite+pysqlite:///:memory:")
    database.upgrade()
    yield database
    database.engine.dispose()
