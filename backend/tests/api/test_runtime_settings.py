def test_runtime_settings_models_default_to_cloud_only():
    from app.schemas.settings import SettingsView
    from app.schemas.ollama import RuntimeSettingsInput
    runtime = RuntimeSettingsInput()
    assert runtime.routing.mode == "cloud_only"
    assert SettingsView.model_fields["runtime"].is_required() is False

def test_runtime_settings_save_is_atomic_and_visible(client, auth_headers):
    response = client.post("/api/settings/runtime", headers=auth_headers, json={"ollama":{"baseUrl":"http://127.0.0.1:11434","model":"qwen2.5:7b","timeoutSeconds":120},"routing":{"mode":"automatic"}})
    assert response.status_code == 200
    assert response.json()["runtime"]["routing"]["mode"] == "automatic"
    assert response.json()["runtime"]["ollama"]["model"] == "qwen2.5:7b"
