import pytest

from app.api.errors import DomainError
from app.api.settings import SettingsService
from app.core.secrets import MemorySecretStore
from app.schemas.local_model import LocalModelConfig, RoutingSettings, RuntimeSettingsInput
from app.storage.repositories import SettingStore


def test_runtime_settings_models_default_to_cloud_only():
    from app.schemas.local_model import RuntimeSettingsInput
    from app.schemas.settings import SettingsView
    runtime = RuntimeSettingsInput()
    assert runtime.routing.mode == "cloud_only"
    assert SettingsView.model_fields["runtime"].is_required() is False

def test_runtime_settings_save_is_atomic_and_visible(client, auth_headers):
    response = client.post("/api/settings/runtime", headers=auth_headers, json={"local":{"baseUrl":"http://127.0.0.1:11434","model":"qwen2.5:7b","timeoutSeconds":120},"routing":{"mode":"automatic"}})
    assert response.status_code == 200
    assert response.json()["runtime"]["routing"]["mode"] == "automatic"
    assert response.json()["runtime"]["local"]["model"] == "qwen2.5:7b"


@pytest.mark.asyncio
async def test_runtime_save_rejects_dns_rebinding_without_overwriting_previous(database):
    class Resolver:
        async def resolve(self, host):
            return ["127.0.0.1"] if host == "127.0.0.1" else ["127.0.0.1", "192.168.1.4"]

    service = SettingsService(
        SettingStore(database), MemorySecretStore(), resolver=Resolver()
    )
    valid = RuntimeSettingsInput(
        local=LocalModelConfig(model="qwen2.5:7b"),
        routing=RoutingSettings(mode="automatic"),
    )
    await service.save_runtime(valid)
    invalid = RuntimeSettingsInput(
        local=LocalModelConfig(base_url="http://localhost:11434", model="other"),
        routing=RoutingSettings(mode="local_only"),
    )
    with pytest.raises(DomainError) as raised:
        await service.save_runtime(invalid)
    assert raised.value.code == "LOCAL_MODEL_UNAVAILABLE"
    assert service.runtime().local.model == "qwen2.5:7b"
    assert service.runtime().routing.mode == "automatic"


@pytest.mark.asyncio
async def test_non_ollama_local_server_is_saved_intact(database):
    """A local server is identified by its port, not by being Ollama.

    LM Studio listens on 1234 and llama.cpp's server on 8080; pinning the port
    to 11434 made every other local server unsaveable.
    """

    class Resolver:
        async def resolve(self, host):
            return ["127.0.0.1"]

    service = SettingsService(
        SettingStore(database), MemorySecretStore(), resolver=Resolver()
    )
    for base_url in ("http://127.0.0.1:11434", "http://127.0.0.1:1234", "http://127.0.0.1:8080"):
        saved = await service.save_runtime(
            RuntimeSettingsInput(local=LocalModelConfig(base_url=base_url, model="m"))
        )
        assert saved.local.base_url == base_url
        assert service.runtime().local.base_url == base_url


@pytest.mark.asyncio
async def test_legacy_ollama_config_key_is_still_read(database):
    """An install configured before the rename keeps its server and model."""
    store = SettingStore(database)
    store.set(
        "ollama.config",
        '{"baseUrl":"http://127.0.0.1:11434","model":"qwen2.5:7b","timeoutSeconds":120}',
    )
    service = SettingsService(store, MemorySecretStore(), resolver=None)
    assert service.runtime().local.model == "qwen2.5:7b"
    assert service.runtime().local.base_url == "http://127.0.0.1:11434"
