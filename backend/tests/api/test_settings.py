from __future__ import annotations

import json
from pathlib import Path

from app.core.llm import ModelConnectionResult
from app.core.secrets import KeyringSecretStore, MemorySecretStore
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
