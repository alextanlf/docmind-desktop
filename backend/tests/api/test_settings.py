from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.api.errors import DomainError
from app.api.settings import MODEL_CONFIG_KEY, MODEL_KEY_REFERENCE, SettingsService
from app.core.llm import AvailableModel, ModelConnectionResult
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
    # 升级前的旧槽位属于「已保存的那个预设」，所以它报 True，而别的预设报 False。
    assert response.json()["apiKeys"]["custom"] is True
    assert response.json()["apiKeys"]["deepseek"] is False


def test_api_keys_are_stored_per_preset_and_never_shared(
    client, auth_headers, app_secret_store: MemorySecretStore
) -> None:
    """🔴 回归：key 曾经只有一把全局槽位，「已安全保存」于是跨厂商成立。

    实测表现：在 DeepSeek 存好 key 后切到 Kimi，界面仍显示「已安全保存，留空可
    保留」，测试连接按钮也照常可点 —— 可那把 key 是 DeepSeek 的，打不通 Kimi。
    """
    saved = client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "deepseek",
            "baseUrl": "https://api.deepseek.com/v1",
            "model": "deepseek-flash",
            "timeoutSeconds": 30,
            "apiKey": "deepseek-secret",
        },
    )
    assert saved.status_code == 200

    # 切到另一家，不带 key：它不能沿用 DeepSeek 那把。
    switched = client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "kimi",
            "baseUrl": "https://api.moonshot.cn/v1",
            "model": "kimi-k3",
            "timeoutSeconds": 30,
        },
    )

    assert switched.status_code == 200
    payload = switched.json()
    assert payload["hasApiKey"] is False
    assert payload["apiKeys"]["kimi"] is False
    # 原来那家的 key 还在，切回去就能用 —— 只是不再冒充新预设的。
    assert payload["apiKeys"]["deepseek"] is True
    assert app_secret_store.get("model-api-key:deepseek") == "deepseek-secret"
    assert app_secret_store.get("model-api-key:kimi") is None


def test_legacy_single_slot_key_migrates_to_the_preset_that_owned_it(
    client, auth_headers, app_secret_store: MemorySecretStore
) -> None:
    """升级前只有一把全局 key，它属于当时保存的那个预设；首次写入时搬走。"""
    client.app.state.settings_service.setting_store.set(
        MODEL_CONFIG_KEY,
        json.dumps(
            {
                "preset": "deepseek",
                "baseUrl": "https://api.deepseek.com/v1",
                "model": "deepseek-flash",
                "timeoutSeconds": 30,
                "reasoningEffort": "",
            }
        ),
    )
    app_secret_store.set("model-api-key", "legacy-secret")

    # 迁移前就读得到；而且只对它所属的预设有效，别的预设拿不到。
    before = client.get("/api/settings", headers=auth_headers).json()
    assert before["apiKeys"]["deepseek"] is True
    assert before["apiKeys"]["kimi"] is False

    client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "deepseek",
            "baseUrl": "https://api.deepseek.com/v1",
            "model": "deepseek-flash",
            "timeoutSeconds": 30,
        },
    )

    assert app_secret_store.get("model-api-key") is None
    assert app_secret_store.get("model-api-key:deepseek") == "legacy-secret"


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
    assert app_secret_store.get("model-api-key:deepseek") == "secret-value"
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
    assert app_secret_store.get("model-api-key:custom") == api_key
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
    # 迁移把旧槽位挪到了它所属的预设名下，回滚只回滚这次写入的那把。
    assert secret_store.get("model-api-key:custom") == "old-secret"


def test_presets_supply_editable_defaults(client, auth_headers) -> None:
    response = client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "deepseek",
            "baseUrl": "https://gateway.example/v1",
            "model": "deepseek-v4-pro",
            "timeoutSeconds": 30,
        },
    )

    assert response.status_code == 200
    assert response.json()["model"] == {
        "preset": "deepseek",
        "baseUrl": "https://gateway.example/v1",
        "model": "deepseek-v4-pro",
        "timeoutSeconds": 30,
        # Unset effort resolves to the vendor's documented default on save.
        "reasoningEffort": "high",
    }


def test_skip_model_setup_is_protected_by_runtime_token(client) -> None:
    assert client.post("/api/settings/model/skip-setup").status_code == 401


def test_skipping_model_setup_is_persisted_in_the_settings_view(client, auth_headers) -> None:
    response = client.post("/api/settings/model/skip-setup", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["modelSetupSkipped"] is True
    # A later read reports the same, so the dialog stays dismissed across restarts.
    assert client.get("/api/settings", headers=auth_headers).json()["modelSetupSkipped"] is True


def test_saving_model_config_clears_a_previous_skip(client, auth_headers) -> None:
    client.post("/api/settings/model/skip-setup", headers=auth_headers)

    response = client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "kimi",
            "baseUrl": "https://api.moonshot.cn/v1",
            "model": "kimi-k3",
            "timeoutSeconds": 30,
        },
    )

    assert response.status_code == 200
    assert response.json()["modelSetupSkipped"] is False


def test_model_setup_is_not_marked_skipped_by_default(client, auth_headers) -> None:
    assert client.get("/api/settings", headers=auth_headers).json()["modelSetupSkipped"] is False


def test_model_list_is_protected_by_runtime_token(client) -> None:
    assert client.post("/api/settings/model/list", json={}).status_code == 401


def test_model_list_serves_curated_choices_before_any_key_is_saved(
    client, auth_headers
) -> None:
    """The picker must have options on first paint, with no API key configured."""
    client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "kimi",
            "baseUrl": "https://api.moonshot.cn/v1",
            "model": "kimi-k3",
            "timeoutSeconds": 30,
        },
    )

    response = client.post("/api/settings/model/list", headers=auth_headers)

    assert response.status_code == 200
    ids = [model["id"] for model in response.json()["models"]]
    assert "kimi-k3" in ids
    assert response.json()["models"][0]["label"] == "Kimi K3"


def test_model_list_uses_live_provider_models_and_keeps_saved_model(
    client, auth_headers, app_secret_store: MemorySecretStore
) -> None:
    app_secret_store.set("model-api-key:glm", "k")

    class FakeProvider:
        def __init__(self, config, api_key):
            self.config = config

        async def test_connection(self) -> ModelConnectionResult:  # pragma: no cover
            raise NotImplementedError

        async def list_models(self) -> list[AvailableModel]:
            return [AvailableModel(id="glm-5.3", label="GLM-5.3")]

    client.app.state.settings_service.provider_factory = FakeProvider
    client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "glm",
            "baseUrl": "https://open.bigmodel.cn/api/paas/v4",
            "model": "glm-4.6",
            "timeoutSeconds": 30,
        },
    )

    response = client.post("/api/settings/model/list", json={}, headers=auth_headers)

    assert response.status_code == 200
    ids = [model["id"] for model in response.json()["models"]]
    # Live list first, and the already-selected model stays selectable even
    # though the provider did not report it.
    assert ids == ["glm-5.3", "glm-4.6"]


def test_model_list_falls_back_to_curated_when_provider_rejects_listing(
    client, auth_headers, app_secret_store: MemorySecretStore
) -> None:
    app_secret_store.set("model-api-key:mimo", "k")

    class FakeProvider:
        def __init__(self, config, api_key):
            self.config = config

        async def test_connection(self) -> ModelConnectionResult:  # pragma: no cover
            raise NotImplementedError

        async def list_models(self) -> list[AvailableModel]:
            raise DomainError("MODEL_AUTH_FAILED", "bad key", 401)

    client.app.state.settings_service.provider_factory = FakeProvider
    client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "mimo",
            "baseUrl": "https://api.xiaomimimo.com/v1",
            "model": "mimo-v2.6-pro",
            "timeoutSeconds": 30,
        },
    )

    response = client.post("/api/settings/model/list", json={}, headers=auth_headers)

    assert response.status_code == 200
    assert "mimo-v2.6-pro" in [model["id"] for model in response.json()["models"]]


def test_model_list_uses_the_presets_curated_catalogue_before_anything_is_saved(
    client, auth_headers
) -> None:
    """全新安装时，列表必须跟着**表单里选的预设**走。

    回归：`ModelListProbe` 早期没有 preset 字段，`list_models` 只能退回
    `saved.preset`，而未保存过时它是 `custom` —— 目录为空元组，于是无论用户
    选哪家、Base URL 填什么都返回空列表，前端显示「该服务商未返回模型列表」。
    """

    class FakeProvider:
        def __init__(self, config, api_key):  # pragma: no cover - no key, unused
            raise AssertionError("没有 key 时不该构造 provider")

        async def test_connection(self) -> ModelConnectionResult:  # pragma: no cover
            raise NotImplementedError

        async def list_models(self) -> list[AvailableModel]:  # pragma: no cover
            raise AssertionError("没有 key 时不该请求 /models")

    client.app.state.settings_service.provider_factory = FakeProvider

    for preset, expected in (
        ("openai", "gpt-5.6-terra"),
        ("qwen", "qwen3.8-max"),
        ("deepseek", "deepseek-flash"),
        ("opencode_zen", "mimo-v2.6-flash-free"),
    ):
        response = client.post(
            "/api/settings/model/list",
            headers=auth_headers,
            json={"preset": preset},
        )
        assert response.status_code == 200, preset
        ids = [model["id"] for model in response.json()["models"]]
        assert expected in ids, f"{preset} 的目录没返回：{ids}"
        # 没有 key 时是内置目录，不能谎称来自服务商。
        assert response.json()["source"] == "curated", preset


def test_model_list_reports_why_it_fell_back_to_the_curated_list(
    client, auth_headers, app_secret_store: MemorySecretStore
) -> None:
    """实时获取失败的原因必须透出，不能静默回落。"""

    class FakeProvider:
        def __init__(self, config, api_key):
            self.config = config

        async def test_connection(self) -> ModelConnectionResult:  # pragma: no cover
            raise NotImplementedError

        async def list_models(self) -> list[AvailableModel]:
            raise DomainError("MODEL_AUTH_FAILED", "API Key 无效", 401)

    app_secret_store.set("model-api-key:openai", "bad-key")
    client.app.state.settings_service.provider_factory = FakeProvider
    client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "openai",
            "baseUrl": "https://api.openai.com/v1",
            "model": "gpt-5.6-terra",
            "timeoutSeconds": 30,
        },
    )

    response = client.post(
        "/api/settings/model/list", headers=auth_headers, json={"preset": "openai"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "curated"
    assert "API Key 无效" in payload["notice"]
    # 目录仍然可用，不能因为实时失败就让选择器空掉。
    assert "gpt-5.6-terra" in [model["id"] for model in payload["models"]]


def test_model_list_marks_a_successful_live_fetch(client, auth_headers, app_secret_store: MemorySecretStore) -> None:
    app_secret_store.set("model-api-key:openai", "k")

    class FakeProvider:
        def __init__(self, config, api_key):
            self.config = config

        async def test_connection(self) -> ModelConnectionResult:  # pragma: no cover
            raise NotImplementedError

        async def list_models(self) -> list[AvailableModel]:
            return [AvailableModel(id="gpt-5.6-terra", label="GPT-5.6 Terra")]

    client.app.state.settings_service.provider_factory = FakeProvider
    client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "openai",
            "baseUrl": "https://api.openai.com/v1",
            "model": "gpt-5.6-terra",
            "timeoutSeconds": 30,
        },
    )

    response = client.post(
        "/api/settings/model/list", headers=auth_headers, json={"preset": "openai"}
    )

    assert response.status_code == 200
    assert response.json()["source"] == "live"
    assert response.json()["notice"] is None


def test_model_list_explains_an_empty_catalogue(client, auth_headers) -> None:
    """custom 预设没有内置目录，必须说清是「没 key 且没目录」而不是「服务商没返回」。"""
    response = client.post(
        "/api/settings/model/list", headers=auth_headers, json={"preset": "custom"}
    )

    assert response.status_code == 200
    assert response.json()["models"] == []
    assert response.json()["source"] == "curated"
    assert response.json()["notice"]


def test_settings_view_exposes_curated_models_for_every_preset(client, auth_headers) -> None:
    response = client.get("/api/settings", headers=auth_headers)

    assert response.status_code == 200
    presets = response.json()["modelPresets"]
    assert {"deepseek", "qwen", "kimi", "glm", "mimo", "openai"} <= set(presets)
    assert "custom" not in presets


def test_settings_view_keeps_zen_picker_free_only(client, auth_headers) -> None:
    response = client.get("/api/settings", headers=auth_headers)

    assert response.status_code == 200
    zen = [model["id"] for model in response.json()["modelPresets"]["opencode_zen"]]
    assert zen
    assert all(model.endswith("-free") or model == "big-pickle" for model in zen)


# ------------------------------------------------- OpenCode Zen 仅免费档
def _zen_provider_factory(reported: list[AvailableModel]):
    class FakeProvider:
        def __init__(self, config, api_key):
            self.config = config

        async def test_connection(self) -> ModelConnectionResult:  # pragma: no cover
            raise NotImplementedError

        async def list_models(self) -> list[AvailableModel]:
            return list(reported)

    return FakeProvider


def test_zen_model_list_drops_paid_ids_from_the_live_response(
    client, auth_headers, app_secret_store: MemorySecretStore
) -> None:
    """Zen 的 /models 会返回全部 86 个 id（含付费），必须过滤后才给前端。"""
    app_secret_store.set("model-api-key:opencode_zen", "k")
    client.app.state.settings_service.provider_factory = _zen_provider_factory(
        [
            AvailableModel(id="kimi-k3", label="Kimi K3"),
            AvailableModel(id="mimo-v2.6-flash-free", label="MiMo V2.6 Flash Free"),
            AvailableModel(id="gpt-5.6-terra", label="GPT-5.6 Terra"),
        ]
    )
    client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "opencode_zen",
            "baseUrl": "https://opencode.ai/zen/v1",
            "model": "mimo-v2.6-flash-free",
            "timeoutSeconds": 30,
        },
    )

    response = client.post("/api/settings/model/list", json={}, headers=auth_headers)

    assert response.status_code == 200
    assert [model["id"] for model in response.json()["models"]] == ["mimo-v2.6-flash-free"]


def test_zen_model_list_falls_back_to_curated_when_no_free_model_is_live(
    client, auth_headers, app_secret_store: MemorySecretStore
) -> None:
    """免费档全部轮换下线时，实时列表为空也不能让选择器变空。"""
    app_secret_store.set("model-api-key:opencode_zen", "k")
    client.app.state.settings_service.provider_factory = _zen_provider_factory(
        [AvailableModel(id="kimi-k3", label="Kimi K3")]
    )
    client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "opencode_zen",
            "baseUrl": "https://opencode.ai/zen/v1",
            "model": "mimo-v2.6-flash-free",
            "timeoutSeconds": 30,
        },
    )

    response = client.post("/api/settings/model/list", json={}, headers=auth_headers)

    assert response.status_code == 200
    ids = [model["id"] for model in response.json()["models"]]
    assert ids
    assert all(model.endswith("-free") or model == "big-pickle" for model in ids)


def test_zen_saved_paid_model_is_not_re_added_to_the_picker(
    client, auth_headers, app_secret_store: MemorySecretStore
) -> None:
    """已存的付费 id 不该因为「保持可选」而被塞回免费档列表。"""
    app_secret_store.set("model-api-key:opencode_zen", "k")
    client.app.state.settings_service.provider_factory = _zen_provider_factory(
        [AvailableModel(id="space-bunny-free", label="Space Bunny Free")]
    )
    client.app.state.settings_service.setting_store.set(
        MODEL_CONFIG_KEY,
        json.dumps(
            {
                "preset": "opencode_zen",
                "baseUrl": "https://opencode.ai/zen/v1",
                "model": "kimi-k3",
                "timeoutSeconds": 30,
                "reasoningEffort": "",
            }
        ),
    )

    response = client.post("/api/settings/model/list", json={}, headers=auth_headers)

    assert response.status_code == 200
    assert [model["id"] for model in response.json()["models"]] == ["space-bunny-free"]


def test_saving_a_paid_model_on_zen_is_rejected(
    client, auth_headers, app_secret_store: MemorySecretStore
) -> None:
    app_secret_store.set("model-api-key:opencode_zen", "k")

    response = client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "opencode_zen",
            "baseUrl": "https://opencode.ai/zen/v1",
            "model": "kimi-k3",
            "timeoutSeconds": 30,
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MODEL_NOT_FREE"


def test_saving_a_free_model_on_zen_is_accepted(
    client, auth_headers, app_secret_store: MemorySecretStore
) -> None:
    app_secret_store.set("model-api-key:opencode_zen", "k")

    response = client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "opencode_zen",
            "baseUrl": "https://opencode.ai/zen/v1",
            "model": "mimo-v2.6-flash-free",
            "timeoutSeconds": 30,
        },
    )

    assert response.status_code == 200
    assert response.json()["model"]["model"] == "mimo-v2.6-flash-free"


def test_paid_models_are_still_savable_elsewhere(
    client, auth_headers, app_secret_store: MemorySecretStore
) -> None:
    """限制只针对 Zen，不能误伤 OpenAI / Kimi 等付费厂商。"""
    app_secret_store.set("model-api-key:openai", "k")

    response = client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "openai",
            "baseUrl": "https://api.openai.com/v1",
            "model": "gpt-5.6-terra",
            "timeoutSeconds": 30,
        },
    )

    assert response.status_code == 200
    assert response.json()["model"]["model"] == "gpt-5.6-terra"


def test_model_connection_returns_latency_without_real_network(
    client, auth_headers, app_secret_store: MemorySecretStore
) -> None:
    class FakeProvider:
        async def test_connection(self) -> ModelConnectionResult:
            return ModelConnectionResult(connected=True, latency_ms=7)

    app_secret_store.set("model-api-key:custom", "secret-value")
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


class TestRagSettings:
    """RAG limits used to be env-only: AppSettings carried them but nothing
    persisted them, so the settings UI had no way to change them."""

    def test_runtime_exposes_defaults_on_a_fresh_install(
        self, client, auth_headers
    ) -> None:
        response = client.get("/api/settings", headers=auth_headers)

        assert response.status_code == 200
        assert response.json()["runtime"]["rag"] == {
            "maxSources": 5,
            "memoryRecallMinSimilarity": 0.65,
        }

    def test_saved_limits_round_trip(self, client, auth_headers) -> None:
        current = client.get("/api/settings", headers=auth_headers).json()["runtime"]
        response = client.post(
            "/api/settings/runtime",
            headers=auth_headers,
            json={**current, "rag": {"maxSources": 12, "memoryRecallMinSimilarity": 0.4}},
        )

        assert response.status_code == 200
        reloaded = client.get("/api/settings", headers=auth_headers).json()["runtime"]["rag"]
        assert reloaded == {"maxSources": 12, "memoryRecallMinSimilarity": 0.4}

    @pytest.mark.parametrize(
        "rag",
        [
            {"maxSources": 0, "memoryRecallMinSimilarity": 0.65},
            {"maxSources": 21, "memoryRecallMinSimilarity": 0.65},
            {"maxSources": 5, "memoryRecallMinSimilarity": 1.5},
        ],
    )
    def test_out_of_range_limits_are_rejected(self, client, auth_headers, rag) -> None:
        current = client.get("/api/settings", headers=auth_headers).json()["runtime"]
        response = client.post(
            "/api/settings/runtime", headers=auth_headers, json={**current, "rag": rag}
        )

        assert response.status_code == 422


def test_model_capabilities_are_keyed_by_preset_then_model(
    client, auth_headers
) -> None:
    """The wire shape is preset -> model id -> capabilities.

    Declaring the inner value as a bare ModelPresetCapabilities makes pydantic
    silently discard each model's entry and emit default-empty capabilities under
    every preset. Nothing raised; the renderer just failed schema validation and
    showed a dead-end "无法读取首次设置" dialog with a retry loop.
    """
    capabilities = client.get("/api/settings", headers=auth_headers).json()[
        "modelCapabilities"
    ]

    # Known data that must survive serialization: deepseek-flash exposes
    # off/low/high/max and defaults to high.
    deepseek = capabilities["deepseek"]
    assert isinstance(deepseek, dict)
    assert deepseek["deepseek-flash"] == {
        "reasoningLevels": ["off", "low", "high", "max"],
        "defaultReasoningEffort": "high",
    }
    assert deepseek["deepseek-v4-pro"]["reasoningLevels"], "每个 preset 下每个 model 都要有真实档位"

    # kimi-k3 differs from kimi-k2.7-code: the per-model keying is the whole
    # point, so a collapse to one entry per preset must fail this test.
    assert capabilities["kimi"]["kimi-k3"]["reasoningLevels"] == ["low", "high", "max"]
    assert capabilities["kimi"]["kimi-k2.7-code"]["reasoningLevels"] == []


def test_model_capabilities_cover_every_catalogued_model(
    client, auth_headers
) -> None:
    payload = client.get("/api/settings", headers=auth_headers).json()

    capabilities = payload["modelCapabilities"]
    for preset, models in payload["modelPresets"].items():
        assert set(capabilities.get(preset, {})) == {model["id"] for model in models}
