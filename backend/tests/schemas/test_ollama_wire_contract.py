import pytest

from app.schemas.ollama import GenerationRoute, RuntimeSettingsInput


def test_wire_models_serialize_runtime_and_optional_route_in_camel_case():
    runtime = RuntimeSettingsInput(
        ollama={"baseUrl": "http://127.0.0.1:11434", "model": "qwen2.5:7b", "timeoutSeconds": 120},
        routing={"mode": "automatic"},
    )
    assert runtime.model_dump(by_alias=True)["ollama"]["timeoutSeconds"] == 120
    assert GenerationRoute(source="cloud", model="deepseek-chat", mode="automatic", fallback_reason="OLLAMA_UNAVAILABLE").model_dump(by_alias=True)["fallbackReason"] == "OLLAMA_UNAVAILABLE"

def test_runtime_rejects_non_loopback_url():
    with pytest.raises(ValueError): RuntimeSettingsInput(ollama={"baseUrl":"http://10.0.0.1:11434","model":"m","timeoutSeconds":120}, routing={"mode":"local_only"})
