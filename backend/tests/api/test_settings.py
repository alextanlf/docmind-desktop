from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.api.errors import DomainError
from app.api.settings import MODEL_CONFIG_KEY, MODEL_KEY_REFERENCE, SettingsService
from app.core.llm import ModelConnectionResult
from app.core.secrets import KeyringSecretStore, MemorySecretStore
from app.schemas.settings import ModelSettingsUpdate
from app.storage.models import SettingRecord


def test_settings_are_protected_by_runtime_token(client) -> None:
    response = client.get("/api/settings")

    assert response.status_code == 401


def test_all_settings_mutations_are_protected_by_runtime_token(client) -> None:
    responses = [
        client.put(
            "/api/settings/model",
            json={"preset": "custom", "baseUrl": "", "model": "", "timeoutSeconds": 30},
        ),
        client.post("/api/settings/model/test"),
        client.post("/api/settings/diagnostics/clear"),
    ]

    assert [response.status_code for response in responses] == [401, 401, 401]


def test_settings_view_redacts_key_and_reports_absolute_data_path(
    client, auth_headers, app_settings, app_secret_store: MemorySecretStore
) -> None:
    app_secret_store.set("model-api-key", "secret-value")

    response = client.get("/api/settings", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["hasApiKey"] is True
    assert response.json()["dataPath"] == str(app_settings.data_dir.resolve())
    assert response.json()["screenshotCount"] == 0
    assert "secret-value" not in response.text
    assert "apiKey" not in response.json()


def test_keychain_backend_error_is_redacted_from_settings_response(client, auth_headers, monkeypatch) -> None:
    backend_detail = "keychain backend rejected secret-value"

    def fail_get_password(service: str, account: str) -> None:
        del service, account
        raise RuntimeError(backend_detail)

    monkeypatch.setattr("app.core.secrets.keyring.get_password", fail_get_password)
    client.app.state.settings_service.secret_store = KeyringSecretStore()

    response = client.get("/api/settings", headers=auth_headers)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SECRET_STORE_FAILED"
    assert backend_detail not in response.text
    assert "secret-value" not in response.text


def test_saving_model_config_persists_only_non_secret_values(
    client, auth_headers, app_secret_store: MemorySecretStore
) -> None:
    response = client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "deepseek",
            "baseUrl": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
            "timeoutSeconds": 30,
            "apiKey": "secret-value",
        },
    )

    assert response.status_code == 200
    assert response.json()["hasApiKey"] is True
    assert "secret-value" not in response.text
    assert app_secret_store.get("model-api-key") == "secret-value"
    with client.app.state.database.session() as session:
        values = [record.value for record in session.query(SettingRecord).all()]
    assert "secret-value" not in json.dumps(values)
    assert "model-api-key" in json.dumps(values)


def test_very_long_api_key_is_never_reflected_in_response_or_sqlite(
    client, auth_headers, app_secret_store: MemorySecretStore
) -> None:
    api_key = "secret-value-" * 1000

    response = client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "custom",
            "baseUrl": "https://example.test/v1",
            "model": "test-model",
            "timeoutSeconds": 30,
            "apiKey": api_key,
        },
    )

    assert response.status_code == 200
    assert api_key not in response.text
    assert app_secret_store.get("model-api-key") == api_key
    with client.app.state.database.session() as session:
        values = [record.value for record in session.query(SettingRecord).all()]
    assert api_key not in json.dumps(values)


@pytest.mark.parametrize(
    "base_url",
    [
        "https://user:base-url-secret@example.test/v1",
        "https://example.test/v1?api_key=base-url-secret",
        "https://example.test/v1#base-url-secret",
        "ftp://example.test/base-url-secret",
    ],
)
def test_unsafe_model_base_url_is_rejected_without_persisting_or_reflecting_secrets(
    client, auth_headers, base_url: str
) -> None:
    response = client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "custom",
            "baseUrl": base_url,
            "model": "test-model",
            "timeoutSeconds": 30,
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert "base-url-secret" not in response.text
    with client.app.state.database.session() as session:
        values = [record.value for record in session.query(SettingRecord).all()]
    assert "base-url-secret" not in json.dumps(values)


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        (
            "HTTPS://EXAMPLE.TEST:443/compatible-mode/v1/",
            "https://example.test/compatible-mode/v1",
        ),
        ("http://EXAMPLE.TEST:80/v1/", "http://example.test/v1"),
    ],
)
def test_model_base_url_is_normalized_without_discarding_compatible_path(
    client, auth_headers, base_url: str, expected: str
) -> None:
    response = client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "custom",
            "baseUrl": base_url,
            "model": "test-model",
            "timeoutSeconds": 30,
        },
    )

    assert response.status_code == 200
    assert response.json()["model"]["baseUrl"] == expected
    with client.app.state.database.session() as session:
        stored = session.get(SettingRecord, "model.config")
    assert stored is not None
    assert json.loads(stored.value)["baseUrl"] == expected


def test_omitted_key_preserves_existing_key_and_empty_key_deletes_it(
    client, auth_headers, app_secret_store: MemorySecretStore
) -> None:
    app_secret_store.set("model-api-key", "old-secret")
    payload = {
        "preset": "custom",
        "baseUrl": "https://example.test/v1",
        "model": "test-model",
        "timeoutSeconds": 20,
    }

    preserved = client.put("/api/settings/model", headers=auth_headers, json=payload)
    deleted = client.put("/api/settings/model", headers=auth_headers, json={**payload, "apiKey": ""})

    assert preserved.status_code == 200
    assert preserved.json()["hasApiKey"] is True
    assert deleted.status_code == 200
    assert deleted.json()["hasApiKey"] is False
    assert app_secret_store.get("model-api-key") is None


async def test_keychain_failure_keeps_previous_model_config() -> None:
    old_config = (
        '{"preset":"custom","baseUrl":"https://old.example/v1",'
        '"model":"old-model","timeoutSeconds":30}'
    )

    class DictSettingStore:
        def __init__(self) -> None:
            self.values = {
                MODEL_CONFIG_KEY: old_config,
                MODEL_KEY_REFERENCE: "model-api-key",
            }

        def get(self, key: str) -> str | None:
            return self.values.get(key)

        def set(self, key: str, value: str) -> None:
            self.values[key] = value

        def set_many(self, values: dict[str, str]) -> None:
            self.values.update(values)

    class FailingSecretStore(MemorySecretStore):
        def set(self, name: str, value: str) -> None:
            del name, value
            raise DomainError("SECRET_STORE_FAILED", "无法访问系统钥匙串，请稍后重试", 503, True)

    setting_store = DictSettingStore()
    secret_store = FailingSecretStore()
    secret_store._values["model-api-key"] = "old-secret"
    service = SettingsService(setting_store, secret_store)  # type: ignore[arg-type]

    with pytest.raises(DomainError) as error:
        await service.save_model(
            ModelSettingsUpdate(
                preset="custom",
                base_url="https://new.example/v1",
                model="new-model",
                timeout_seconds=30,
                api_key="new-secret",
            )
        )

    assert error.value.code == "SECRET_STORE_FAILED"
    assert setting_store.values[MODEL_CONFIG_KEY] == old_config
    assert secret_store.get("model-api-key") == "old-secret"


async def test_settings_persistence_failure_restores_previous_api_key() -> None:
    old_config = (
        '{"preset":"custom","baseUrl":"https://old.example/v1",'
        '"model":"old-model","timeoutSeconds":30}'
    )

    class FailingSettingStore:
        def __init__(self) -> None:
            self.values = {
                MODEL_CONFIG_KEY: old_config,
                MODEL_KEY_REFERENCE: "model-api-key",
            }

        def get(self, key: str) -> str | None:
            return self.values.get(key)

        def set(self, key: str, value: str) -> None:
            self.values[key] = value
            if key == MODEL_KEY_REFERENCE:
                raise RuntimeError("settings commit failed")

        def set_many(self, values: dict[str, str]) -> None:
            del values
            raise RuntimeError("settings commit failed")

    setting_store = FailingSettingStore()
    secret_store = MemorySecretStore()
    secret_store.set("model-api-key", "old-secret")
    service = SettingsService(setting_store, secret_store)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="settings commit failed"):
        await service.save_model(
            ModelSettingsUpdate(
                preset="custom",
                base_url="https://new.example/v1",
                model="new-model",
                timeout_seconds=30,
                api_key="new-secret",
            )
        )

    assert setting_store.values == {
        MODEL_CONFIG_KEY: old_config,
        MODEL_KEY_REFERENCE: "model-api-key",
    }
    assert secret_store.get("model-api-key") == "old-secret"


def test_presets_supply_editable_defaults(client, auth_headers) -> None:
    response = client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "deepseek",
            "baseUrl": "https://gateway.example/v1",
            "model": "my-deepseek",
            "timeoutSeconds": 30,
        },
    )

    assert response.status_code == 200
    assert response.json()["model"] == {
        "preset": "deepseek",
        "baseUrl": "https://gateway.example/v1",
        "model": "my-deepseek",
        "timeoutSeconds": 30,
    }


def test_model_connection_returns_latency_without_real_network(
    client, auth_headers, app_secret_store: MemorySecretStore
) -> None:
    class FakeProvider:
        async def test_connection(self) -> ModelConnectionResult:
            return ModelConnectionResult(connected=True, latency_ms=7)

    app_secret_store.set("model-api-key", "secret-value")
    client.app.state.settings_service.provider_factory = lambda _, __: FakeProvider()

    response = client.post("/api/settings/model/test", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {"connected": True, "latencyMs": 7}


def test_clear_diagnostics_deletes_only_regular_png_children(
    client, auth_headers, app_settings
) -> None:
    screenshot = app_settings.screenshots_dir / "failure.png"
    text_file = app_settings.screenshots_dir / "notes.txt"
    nested = app_settings.screenshots_dir / "nested"
    nested_png = nested / "nested.png"
    document = app_settings.documents_dir / "guide.md"
    screenshot.write_bytes(b"png")
    text_file.write_text("leave me")
    nested.mkdir()
    nested_png.write_bytes(b"png")
    document.write_text("# Guide")

    response = client.post("/api/settings/diagnostics/clear", headers=auth_headers)

    assert response.status_code == 204
    assert not screenshot.exists()
    assert text_file.exists()
    assert nested_png.exists()
    assert document.exists()


def test_clear_diagnostics_removes_json_and_html_but_not_symlinks(
    client, auth_headers, app_settings, tmp_path: Path
) -> None:
    directory = app_settings.screenshots_dir
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "a.png").write_bytes(b"png")
    (directory / "a.json").write_text("{}", encoding="utf-8")
    (directory / "a.html").write_text("<html></html>", encoding="utf-8")
    outside = tmp_path / "outside.html"
    outside.write_text("<html></html>", encoding="utf-8")
    (directory / "link.html").symlink_to(outside)

    response = client.post("/api/settings/diagnostics/clear", headers=auth_headers)

    assert response.status_code == 204
    assert not (directory / "a.png").exists()
    assert not (directory / "a.json").exists()
    assert not (directory / "a.html").exists()
    assert (directory / "link.html").is_symlink()
    assert outside.exists()


def test_clear_diagnostics_refuses_symlinked_children(client, auth_headers, app_settings, tmp_path: Path) -> None:
    target = tmp_path / "outside.png"
    link = app_settings.screenshots_dir / "linked.png"
    target.write_bytes(b"outside")
    link.symlink_to(target)

    response = client.post("/api/settings/diagnostics/clear", headers=auth_headers)

    assert response.status_code == 204
    assert link.is_symlink()
    assert target.exists()


def test_settings_count_ignores_symlinked_diagnostics_root(client, auth_headers, app_settings, tmp_path: Path) -> None:
    outside = tmp_path / "outside-screenshots"
    outside.mkdir()
    external_png = outside / "failure.png"
    external_png.write_bytes(b"png")
    app_settings.screenshots_dir.rmdir()
    app_settings.screenshots_dir.symlink_to(outside, target_is_directory=True)

    response = client.get("/api/settings", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["screenshotCount"] == 0
    assert external_png.exists()


def test_clear_diagnostics_refuses_symlinked_root(client, auth_headers, app_settings, tmp_path: Path) -> None:
    outside = tmp_path / "outside-screenshots"
    outside.mkdir()
    external_png = outside / "failure.png"
    external_png.write_bytes(b"png")
    app_settings.screenshots_dir.rmdir()
    app_settings.screenshots_dir.symlink_to(outside, target_is_directory=True)

    response = client.post("/api/settings/diagnostics/clear", headers=auth_headers)

    assert response.status_code == 204
    assert external_png.exists()
