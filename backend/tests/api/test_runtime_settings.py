import pytest

from app.api.errors import DomainError
from app.api.settings import SettingsService
from app.core.secrets import MemorySecretStore
from app.schemas.ollama import OllamaConfig, RoutingSettings, RuntimeSettingsInput
from app.storage.repositories import SettingStore


def test_runtime_settings_models_default_to_cloud_only():
    from app.schemas.ollama import RuntimeSettingsInput
    from app.schemas.settings import SettingsView
    runtime = RuntimeSettingsInput()
    assert runtime.routing.mode == "cloud_only"
    assert SettingsView.model_fields["runtime"].is_required() is False

def test_runtime_settings_save_is_atomic_and_visible(client, auth_headers):
    response = client.post("/api/settings/runtime", headers=auth_headers, json={"ollama":{"baseUrl":"http://127.0.0.1:11434","model":"qwen2.5:7b","timeoutSeconds":120},"routing":{"mode":"automatic"}})
    assert response.status_code == 200
    assert response.json()["runtime"]["routing"]["mode"] == "automatic"
    assert response.json()["runtime"]["ollama"]["model"] == "qwen2.5:7b"


@pytest.mark.asyncio
async def test_runtime_save_rejects_dns_rebinding_without_overwriting_previous(database):
    class Resolver:
        async def resolve(self, host):
            return ["127.0.0.1"] if host == "127.0.0.1" else ["127.0.0.1", "192.168.1.4"]

    service = SettingsService(
        SettingStore(database), MemorySecretStore(), resolver=Resolver()
    )
    valid = RuntimeSettingsInput(
        ollama=OllamaConfig(model="qwen2.5:7b"),
        routing=RoutingSettings(mode="automatic"),
    )
    await service.save_runtime(valid)
    invalid = RuntimeSettingsInput(
        ollama=OllamaConfig(base_url="http://localhost:11434", model="other"),
        routing=RoutingSettings(mode="local_only"),
    )
    with pytest.raises(DomainError) as raised:
        await service.save_runtime(invalid)
    assert raised.value.code == "OLLAMA_UNAVAILABLE"
    assert service.runtime().ollama.model == "qwen2.5:7b"
    assert service.runtime().routing.mode == "automatic"
