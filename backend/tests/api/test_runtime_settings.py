def test_runtime_settings_models_default_to_cloud_only():
    from app.schemas.settings import SettingsView
    from app.schemas.ollama import RuntimeSettingsInput
    runtime = RuntimeSettingsInput()
    assert runtime.routing.mode == "cloud_only"
    assert SettingsView.model_fields["runtime"].is_required() is False
