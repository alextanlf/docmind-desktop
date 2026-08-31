from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.storage.database import Database

RUNTIME_TOKEN = "test-runtime-token"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[TestClient]:
    monkeypatch.setenv("DOCMIND_SESSION_TOKEN", RUNTIME_TOKEN)
    monkeypatch.setenv("DOCMIND_DATA_DIR", str(tmp_path / "docmind-data"))
    monkeypatch.setenv("DOCMIND_ENVIRONMENT", "test")

    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"X-DocMind-Token": RUNTIME_TOKEN}


@pytest.fixture
def database() -> Iterator[Database]:
    database = Database("sqlite+pysqlite:///:memory:")
    database.upgrade()
    yield database
    database.engine.dispose()
