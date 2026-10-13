from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.api.errors import DomainError
from app.api.settings import (
    MODEL_CONFIG_KEY,
    MODEL_CUSTOM_PROVIDERS_KEY,
    MODEL_KEY_REFERENCE,
    MODEL_PROFILES_KEY,
    SettingsService,
)
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
        client.delete("/api/settings/model/profiles/abc123"),
        client.post("/api/settings/model/activate", json={"profileId": "abc123"}),
        client.post("/api/settings/figure-ocr", json={"enabled": False}),
    ]

    assert [response.status_code for response in responses] == [401, 401, 401, 401, 401, 401]


def test_figure_ocr_defaults_to_on_and_can_be_turned_off(client, auth_headers) -> None:
    """图片识别默认开；关掉之后设置视图如实反映，并且能再打开。

    默认必须是开：关掉会让截图型课件的整页扫描内容从索引里消失（实测一份 53 页课件
    关掉之后正文从 23447 字符降到 15486）。
    """
    assert client.get("/api/settings", headers=auth_headers).json()["figureOcr"] is True

    off = client.post("/api/settings/figure-ocr", json={"enabled": False}, headers=auth_headers)
    assert off.status_code == 200
    assert off.json()["figureOcr"] is False
    # 重新读一次：值要落库，不能只活在这次响应里。
    assert client.get("/api/settings", headers=auth_headers).json()["figureOcr"] is False

    on = client.post("/api/settings/figure-ocr", json={"enabled": True}, headers=auth_headers)
    assert on.json()["figureOcr"] is True


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
    # 没有名字的自定义厂商现在按 custom:default 这一家来报。
    assert response.json()["apiKeys"]["custom:default"] is True
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
    # 没有名字的自定义厂商现在叫 custom:default，槽位也跟着改名。
    assert app_secret_store.get("model-api-key:custom:default") == api_key
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
        stored = session.get(SettingRecord, MODEL_PROFILES_KEY)
    assert stored is not None
    assert json.loads(stored.value)[0]["baseUrl"] == expected


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

    assert setting_store.values[MODEL_CONFIG_KEY] == old_config
    assert setting_store.values[MODEL_KEY_REFERENCE] == "model-api-key"
    # 档案是这次保存的一部分（裸 custom 归到 custom:default 那家），它跟着这次
    # 写入一起失败，所以存储里不该留下半份档案。
    assert set(setting_store.values) == {MODEL_CONFIG_KEY, MODEL_KEY_REFERENCE}
    # 迁移把旧槽位挪到了它所属的预设名下，回滚只回滚这次写入的那把。
    assert secret_store.get("model-api-key:custom:default") == "old-secret"


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
        # label 只对自定义供应商有值：内置预设的名字在前端的内置表里，视图不重复
        # 一份会过期的副本。
        "label": "",
    }




def test_configured_models_start_empty_until_something_is_saved(client, auth_headers) -> None:
    """设置页只列「已配置过的模型」：一条都没配时列表就是空的。

    升级前那张表是一个「模型预设」下拉框，里面全是用户没配过的内置厂商，于是
    「我到底配了什么」在界面上根本看不出来。
    """
    view = client.get("/api/settings", headers=auth_headers).json()

    assert view["models"] == []
    # 一条都没有时，上层（聊天、路由）读到的仍是那份空配置，照旧报「请先配置 API Key」。
    assert view["model"]["preset"] == "custom:default"
    assert view["hasApiKey"] is False


def test_saved_models_are_listed_with_provider_and_model_name(client, auth_headers) -> None:
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
    client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "custom:new",
            "label": "公司网关",
            "baseUrl": "https://gateway.example/v1",
            "model": "gpt-5.6-terra",
            "timeoutSeconds": 30,
        },
    )

    view = client.get("/api/settings", headers=auth_headers).json()
    gateway = view["models"][1]

    # 每行都带着供应商与模型名，顺序就是保存顺序；刚配的那条是当前使用的。
    assert [(item["model"], item["label"], item["active"]) for item in view["models"]] == [
        ("deepseek-flash", "", False),
        ("gpt-5.6-terra", "公司网关", True),
    ]
    assert view["models"][0]["preset"] == "deepseek"
    assert gateway["preset"].startswith("custom:")
    assert view["model"]["preset"] == gateway["preset"]


def test_two_custom_models_keep_their_own_name_and_api_key(
    client, auth_headers, app_secret_store: MemorySecretStore
) -> None:
    """🔴 回归：自定义厂商曾经只可能有「一家」。

    实测表现：配好第一家（公司网关）之后再配第二家（某中转站），第二家直接把
    第一家的 Base URL、模型和 API Key 一起覆盖掉 —— preset 枚举里 `custom` 只有
    一个值，钥匙串槽位也就只有 `model-api-key:custom` 一个。
    """
    first = client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "custom:new",
            "label": "公司网关",
            "baseUrl": "https://gateway.example/v1",
            "model": "gpt-5.6-terra",
            "timeoutSeconds": 30,
            "apiKey": "gateway-secret",
        },
    ).json()
    first_preset = first["model"]["preset"]
    # 「新建」是个占位 id，落库时必须换成真 id，否则第二家会把第一家顶掉。
    assert first_preset.startswith("custom:")
    assert first_preset != "custom:new"

    second = client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "custom:new",
            "label": "某中转站",
            "baseUrl": "https://relay.example/v1",
            "model": "deepseek-flash",
            "timeoutSeconds": 45,
            "apiKey": "relay-secret",
        },
    ).json()

    second_preset = second["model"]["preset"]
    assert second_preset not in {first_preset, "custom:new"}
    # 名字、地址、模型都按家存：列表显示的就是用户自己起的名字。
    assert [(item["label"], item["baseUrl"], item["model"]) for item in second["models"]] == [
        ("公司网关", "https://gateway.example/v1", "gpt-5.6-terra"),
        ("某中转站", "https://relay.example/v1", "deepseek-flash"),
    ]
    assert second["model"]["label"] == "某中转站"
    # 两家各有各的 key 槽位，「已安全保存」不再靠一家冒充另一家。
    assert second["apiKeys"][first_preset] is True
    assert second["apiKeys"][second_preset] is True
    assert app_secret_store.get(f"model-api-key:{first_preset}") == "gateway-secret"
    assert app_secret_store.get(f"model-api-key:{second_preset}") == "relay-secret"


def test_saving_the_same_provider_and_model_twice_updates_one_row(client, auth_headers) -> None:
    """同一个「供应商 + 模型」再存一次是编辑，不是攒出第二行。"""
    body = {
        "preset": "deepseek",
        "baseUrl": "https://api.deepseek.com/v1",
        "model": "deepseek-flash",
        "timeoutSeconds": 30,
    }
    client.put("/api/settings/model", headers=auth_headers, json=body)

    again = client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={**body, "baseUrl": "https://gateway.example/v1"},
    ).json()

    assert len(again["models"]) == 1
    assert again["models"][0]["baseUrl"] == "https://gateway.example/v1"
    assert again["model"]["baseUrl"] == "https://gateway.example/v1"


def test_editing_a_model_by_profile_id_updates_it_in_place(client, auth_headers) -> None:
    """「编辑」是改那一条：id 不变，也不新增一行。"""
    created = client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "deepseek",
            "baseUrl": "https://api.deepseek.com/v1",
            "model": "deepseek-flash",
            "timeoutSeconds": 30,
        },
    ).json()
    profile_id = created["models"][0]["id"]

    edited = client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "profileId": profile_id,
            "preset": "deepseek",
            "baseUrl": "https://api.deepseek.com/v1",
            "model": "deepseek-v4-pro",
            "timeoutSeconds": 45,
        },
    ).json()

    assert [(item["id"], item["model"], item["timeoutSeconds"]) for item in edited["models"]] == [
        (profile_id, "deepseek-v4-pro", 45)
    ]
    assert edited["model"]["model"] == "deepseek-v4-pro"


def test_saving_with_an_unknown_profile_id_is_rejected(client, auth_headers) -> None:
    """表单拿着一条已经被删掉的配置在保存：报错，而不是悄悄新建一条同名的。"""
    response = client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "profileId": "nosuchprofile",
            "preset": "deepseek",
            "baseUrl": "https://api.deepseek.com/v1",
            "model": "deepseek-flash",
            "timeoutSeconds": 30,
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "MODEL_PROFILE_NOT_FOUND"
    assert client.get("/api/settings", headers=auth_headers).json()["models"] == []


def test_activating_a_model_switches_what_the_app_uses(client, auth_headers) -> None:
    first = client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "deepseek",
            "baseUrl": "https://api.deepseek.com/v1",
            "model": "deepseek-flash",
            "timeoutSeconds": 30,
        },
    ).json()
    deepseek_id = first["models"][0]["id"]
    client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "custom:new",
            "label": "公司网关",
            "baseUrl": "https://gateway.example/v1",
            "model": "gpt-5.6-terra",
            "timeoutSeconds": 30,
        },
    )

    switched = client.post(
        "/api/settings/model/activate",
        headers=auth_headers,
        json={"profileId": deepseek_id},
    )

    assert switched.status_code == 200
    payload = switched.json()
    assert payload["model"]["preset"] == "deepseek"
    assert [(item["model"], item["active"]) for item in payload["models"]] == [
        ("deepseek-flash", True),
        ("gpt-5.6-terra", False),
    ]


def test_activating_an_unknown_model_is_rejected(client, auth_headers) -> None:
    response = client.post(
        "/api/settings/model/activate", headers=auth_headers, json={"profileId": "nosuchprofile"}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "MODEL_PROFILE_NOT_FOUND"


def test_two_models_of_one_provider_share_the_key_and_deleting_one_keeps_it(
    client, auth_headers, app_secret_store: MemorySecretStore
) -> None:
    """key 属于厂商，不属于某一次选择：同一家的两条配置共用一个槽位。"""
    first = client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "deepseek",
            "baseUrl": "https://api.deepseek.com/v1",
            "model": "deepseek-flash",
            "timeoutSeconds": 30,
            "apiKey": "deepseek-secret",
        },
    ).json()
    assert first["models"][0]["hasApiKey"] is True

    second = client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "deepseek",
            "baseUrl": "https://api.deepseek.com/v1",
            "model": "deepseek-v4-pro",
            "timeoutSeconds": 30,
        },
    ).json()
    assert [(item["model"], item["hasApiKey"]) for item in second["models"]] == [
        ("deepseek-flash", True),
        ("deepseek-v4-pro", True),
    ]

    # 删掉不带 key 的那条：另一条还在用这个槽位，key 必须留着。
    remaining = client.delete(
        f"/api/settings/model/profiles/{second['models'][1]['id']}", headers=auth_headers
    ).json()
    assert app_secret_store.get("model-api-key:deepseek") == "deepseek-secret"
    assert remaining["models"][0]["hasApiKey"] is True

    # 删掉最后一条：槽位跟着走，否则下次再配同一家会被报成「已安全保存」。
    emptied = client.delete(
        f"/api/settings/model/profiles/{remaining['models'][0]['id']}", headers=auth_headers
    ).json()
    assert emptied["models"] == []
    assert app_secret_store.get("model-api-key:deepseek") is None


def test_deleting_the_active_model_falls_back_to_a_remaining_one(client, auth_headers) -> None:
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
    created = client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "custom:new",
            "label": "临时试用",
            "baseUrl": "https://trial.example/v1",
            "model": "trial-model",
            "timeoutSeconds": 30,
            "apiKey": "trial-secret",
        },
    ).json()
    custom_id = created["models"][1]["id"]

    payload = client.delete(
        f"/api/settings/model/profiles/{custom_id}", headers=auth_headers
    ).json()

    # 删掉的正是当前在用的那条：剩下的第一条顶上，而不是留着一条指向已删除记录的
    # active id —— 那样聊天会继续打一个已经不存在的配置。
    assert payload["model"]["preset"] == "deepseek"
    assert [item["active"] for item in payload["models"]] == [True]
    assert not any(key.startswith("custom:") for key in payload["apiKeys"])

    # 一条都不剩时列表为空，上层回落到那份空配置。
    emptied = client.delete(
        f"/api/settings/model/profiles/{payload['models'][0]['id']}", headers=auth_headers
    ).json()
    assert emptied["models"] == []
    assert emptied["model"]["preset"] == "custom:default"


def test_deleting_an_unknown_model_is_rejected(client, auth_headers) -> None:
    response = client.delete("/api/settings/model/profiles/nosuchprofile", headers=auth_headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "MODEL_PROFILE_NOT_FOUND"


def test_legacy_providers_and_config_are_read_into_the_model_list(client, auth_headers) -> None:
    """升级前的两份数据（供应商档案 + 那份唯一配置）读成一张列表，且读路径不写库。"""
    legacy = json.dumps(
        [
            {
                "id": "relay01",
                "label": "某中转站",
                "baseUrl": "https://relay.example/v1",
                "model": "deepseek-flash",
                "timeoutSeconds": 45,
                "reasoningEffort": "",
            }
        ]
    )
    store = client.app.state.settings_service.setting_store
    store.set(MODEL_CUSTOM_PROVIDERS_KEY, legacy)
    store.set(
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

    view = client.get("/api/settings", headers=auth_headers).json()

    # 读路径不写库：老数据原样躺着，只是被报成一张列表。
    assert store.get(MODEL_CUSTOM_PROVIDERS_KEY) == legacy
    assert store.get(MODEL_PROFILES_KEY) is None
    assert [(item["id"], item["preset"], item["model"], item["label"]) for item in view["models"]] == [
        ("relay01", "custom:relay01", "deepseek-flash", "某中转站"),
        ("legacy", "deepseek", "deepseek-flash", ""),
    ]
    # 升级前生效的是那份唯一配置，所以它才是当前使用的那一条。
    assert view["model"]["preset"] == "deepseek"

    # 第一次写操作把它们落成正式列表（id 不变，界面上的按钮因此不会指空）。
    activated = client.post(
        "/api/settings/model/activate", headers=auth_headers, json={"profileId": "relay01"}
    ).json()
    assert [item["preset"] for item in json.loads(store.get(MODEL_PROFILES_KEY))] == [
        "custom:relay01",
        "deepseek",
    ]
    assert activated["model"]["preset"] == "custom:relay01"


def test_legacy_custom_slot_is_read_as_the_unnamed_provider(
    client, auth_headers, app_secret_store: MemorySecretStore
) -> None:
    """升级前裸的 `custom` 与它的旧槽位，读的时候归到固定 id 的「未命名」那家。"""
    legacy_config = json.dumps(
        {
            "preset": "custom",
            "baseUrl": "https://old.example/v1",
            "model": "old-model",
            "timeoutSeconds": 30,
            "reasoningEffort": "",
        }
    )
    store = client.app.state.settings_service.setting_store
    store.set(MODEL_CONFIG_KEY, legacy_config)
    app_secret_store.set("model-api-key:custom", "old-secret")

    view = client.get("/api/settings", headers=auth_headers).json()

    # 读路径不写库：老配置原样躺着，只是被报成 custom:default 那一家的配置。
    assert store.get(MODEL_CONFIG_KEY) == legacy_config
    assert view["model"]["preset"] == "custom:default"
    assert [
        (item["id"], item["preset"], item["label"], item["model"], item["active"])
        for item in view["models"]
    ] == [("default", "custom:default", "自定义", "old-model", True)]
    # 旧槽位只有这一家读得到，别家不能借。
    assert view["apiKeys"]["custom:default"] is True
    assert "custom" not in view["apiKeys"]
    assert view["apiKeys"]["deepseek"] is False

    saved = client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "custom:default",
            "label": "我的中转站",
            "baseUrl": "https://old.example/v1",
            "model": "old-model",
            "timeoutSeconds": 30,
        },
    ).json()

    assert [(item["id"], item["label"]) for item in saved["models"]] == [("default", "我的中转站")]
    assert saved["model"]["label"] == "我的中转站"
    # 旧槽位真的被搬走了：只靠读时回退的话，「清除已保存的 API Key」会删掉新槽位
    # 之后又被旧槽位顶回来 —— 界面说已删除、实际还能用。
    assert app_secret_store.get("model-api-key:custom") is None
    assert app_secret_store.get("model-api-key:custom:default") == "old-secret"

    cleared = client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "custom:default",
            "label": "我的中转站",
            "baseUrl": "https://old.example/v1",
            "model": "old-model",
            "timeoutSeconds": 30,
            "apiKey": "",
        },
    ).json()
    assert cleared["apiKeys"]["custom:default"] is False
    assert app_secret_store.get("model-api-key:custom:default") is None


def test_a_stale_legacy_slot_cannot_resurrect_a_cleared_key(
    client, auth_headers, app_secret_store: MemorySecretStore
) -> None:
    """新槽位已经有 key 时，旧槽位仍然必须被删掉。

    留着它，「清除已保存的 API Key」删的是新槽位，而读的时候又被旧槽位顶回来 ——
    界面说已删除，实际那把 key 还能用。
    """
    client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "custom",
            "baseUrl": "https://old.example/v1",
            "model": "old-model",
            "timeoutSeconds": 30,
            "apiKey": "new-slot-secret",
        },
    )
    # 模拟升级残留：新槽位已有 key，旧槽位还躺着一把过期的。
    app_secret_store.set("model-api-key:custom", "stale-legacy-secret")

    saved = client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "custom:default",
            "label": "我的中转站",
            "baseUrl": "https://old.example/v1",
            "model": "old-model",
            "timeoutSeconds": 30,
        },
    ).json()
    assert app_secret_store.get("model-api-key:custom") is None
    assert app_secret_store.get("model-api-key:custom:default") == "new-slot-secret"
    assert saved["apiKeys"]["custom:default"] is True

    cleared = client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "custom:default",
            "label": "我的中转站",
            "baseUrl": "https://old.example/v1",
            "model": "old-model",
            "timeoutSeconds": 30,
            "apiKey": "",
        },
    ).json()
    assert cleared["apiKeys"]["custom:default"] is False

def test_model_connection_tests_the_form_draft_instead_of_the_saved_model(
    client, auth_headers, app_secret_store: MemorySecretStore
) -> None:
    """二级页面里测的是正在编的那一条，不是当前使用的那条。

    表单草稿带着 preset / 地址 / 模型来测；不带草稿的请求（旧调用）仍测已保存的
    配置，所以两条路径都要成立。
    """
    seen: list[tuple[str, str]] = []

    class FakeProvider:
        def __init__(self, config) -> None:
            seen.append((config.preset, config.model))

        async def test_connection(self) -> ModelConnectionResult:
            return ModelConnectionResult(connected=True, latency_ms=3)

    client.app.state.settings_service.provider_factory = lambda config, _key: FakeProvider(config)
    client.put(
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
    app_secret_store.set("model-api-key:kimi", "kimi-secret")

    drafted = client.post(
        "/api/settings/model/test",
        headers=auth_headers,
        json={"preset": "kimi", "baseUrl": "https://api.moonshot.cn/v1", "model": "kimi-k2.6"},
    )
    saved = client.post("/api/settings/model/test", headers=auth_headers)

    assert [drafted.status_code, saved.status_code] == [200, 200]
    assert seen == [("kimi", "kimi-k2.6"), ("deepseek", "deepseek-flash")]


def test_model_connection_reports_a_missing_key_for_the_drafted_provider(
    client, auth_headers
) -> None:
    """草稿那家没有 key 就报「请先配置 API Key」，而不是借当前在用的那把去打。"""
    client.put(
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

    response = client.post(
        "/api/settings/model/test",
        headers=auth_headers,
        json={"preset": "kimi", "baseUrl": "https://api.moonshot.cn/v1", "model": "kimi-k3"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "MODEL_AUTH_FAILED"



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


def test_model_list_follows_the_probed_custom_provider(client, auth_headers) -> None:
    """🔴 回归：探测自定义预设时不能退回「已保存的那家」。

    实测表现：在下拉框里选了第二家自定义厂商、填好它自己的 key 再点「获取模型
    列表」，拿到的却是第一家的内置目录和第一家的 key —— 那把 key 打不通第二家，
    只会得到一场 401。
    """
    calls: list[tuple[str, str, str]] = []

    class FakeProvider:
        def __init__(self, config, api_key):
            calls.append((config.preset, config.base_url, api_key))

        async def test_connection(self) -> ModelConnectionResult:  # pragma: no cover
            raise NotImplementedError

        async def list_models(self) -> list[AvailableModel]:
            return [AvailableModel(id="relay-model", label="Relay Model")]

    client.app.state.settings_service.provider_factory = FakeProvider
    client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "custom:new",
            "label": "公司网关",
            "baseUrl": "https://gateway.example/v1",
            "model": "gpt-5.6-terra",
            "timeoutSeconds": 30,
            "apiKey": "gateway-key",
        },
    )

    response = client.post(
        "/api/settings/model/list",
        headers=auth_headers,
        json={
            "preset": "custom:new",
            "baseUrl": "https://relay.example/v1",
            "model": "relay-model",
            "apiKey": "relay-key",
        },
    )

    assert response.status_code == 200
    assert response.json()["source"] == "live"
    assert calls == [("custom:new", "https://relay.example/v1", "relay-key")]


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
