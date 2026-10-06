from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import AppSettings
from app.core.secrets import MemorySecretStore
from app.storage.database import Database
from app.storage.repositories import SettingStore
from tests.conftest import RUNTIME_TOKEN


def test_legacy_settings_keys_no_longer_configure_yuque(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("DOCMIND_SESSION_TOKEN", RUNTIME_TOKEN)
    monkeypatch.setenv("DOCMIND_DATA_DIR", str(tmp_path / "legacy-data"))
    monkeypatch.setenv("DOCMIND_ENVIRONMENT", "test")
    settings = AppSettings(
        session_token=SecretStr(RUNTIME_TOKEN),
        data_dir=tmp_path / "legacy-data",
        environment="test",
    )
    database_dir = settings.data_dir / "database"
    database_dir.mkdir(parents=True, exist_ok=True)
    database = Database(f"sqlite+pysqlite:///{database_dir / 'docmind.sqlite3'}")
    database.upgrade()
    setting_store = SettingStore(database)
    setting_store.set("yuque-api.verified", "true")
    setting_store.set("yuque-web.connected", "true")
    database.engine.dispose()

    from app.main import create_app

    with TestClient(create_app(settings, secret_store=MemorySecretStore())) as test_client:
        providers = test_client.get(
            "/api/remote/providers", headers={"X-DocMind-Token": RUNTIME_TOKEN}
        ).json()

    yuque = next(provider for provider in providers if provider["name"] == "yuque")
    assert yuque["configured"] is False