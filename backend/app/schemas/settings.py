from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.core.llm import AvailableModel, ModelConfig, ModelConnectionResult, model_label
from app.schemas.common import WireModel
from app.schemas.ollama import RuntimeSettingsInput
from app.schemas.web_search import WebSearchSettings

MODEL_PRESETS = {
    # DeepSeek retired deepseek-chat / deepseek-reasoner on 2026-07-24; the
    # pricing page now names deepseek-flash and deepseek-v4-pro.
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-flash"),
    "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    "kimi": ("https://api.moonshot.cn/v1", "kimi-k3"),
    "glm": ("https://open.bigmodel.cn/api/paas/v4", "glm-4.6"),
    # MiMo-V2.5 is retired on 2026-10-21 per the vendor's deprecation notice.
    "mimo": ("https://api.xiaomimimo.com/v1", "mimo-v2.6-pro"),
    # OpenCode Zen is pay-as-you-go with a set of free models; Go is the flat
    # monthly plan. Both verified live: GET /models returns 200 without a key.
    "opencode_zen": ("https://opencode.ai/zen/v1", "mimo-v2.5-free"),
    "opencode_go": ("https://opencode.ai/zen/go/v1", "mimo-v2.5"),
    "openai": ("https://api.openai.com/v1", "gpt-5-mini"),
    "custom": ("", ""),
}

# Curated per-preset model catalogue. Lets the settings form offer a real choice
# before an API key exists (and when the provider has no `/models` endpoint),
# while `GET /models` still overrides this with the provider's live list.
# Ids verified against each vendor's official docs and, for OpenCode, against a
# live `GET /models` call. The first entry is the preset default.
MODEL_CATALOG: dict[str, tuple[str, ...]] = {
    "deepseek": ("deepseek-flash", "deepseek-v4-pro"),
    "qwen": ("qwen-plus", "qwen-max", "qwen-turbo", "qwen-flash"),
    "kimi": ("kimi-k3", "kimi-k2.6", "kimi-k2.5", "kimi-k2.7-code"),
    "glm": ("glm-4.6", "glm-5.2", "glm-5.3", "glm-5.3-flash", "glm-4.7"),
    "mimo": ("mimo-v2.6-pro", "mimo-v2.6-flash"),
    # Free tier ids come from a live `GET https://opencode.ai/zen/v1/models`
    # (200 without a key, 86 models, 13 with "free" in the id).
    "opencode_zen": (
        "mimo-v2.5-free",
        "deepseek-v4-flash-free",
        "mimo-v2.6-flash-free",
        "nemotron-3-ultra-free",
        "ling-3.1-flash-free",
        "space-bunny-free",
        "longcat-2.5-preview-free",
        "fledge-alpha-free",
        "big-pickle",
        "kimi-k3",
        "glm-5.3-flash",
    ),
    "opencode_go": (
        "mimo-v2.5",
        "mimo-v2.5-pro",
        "deepseek-v4-flash",
        "deepseek-v4-pro",
        "glm-5.3-flash",
        "kimi-k2.6",
        "longcat-2.0",
        "space-bunny-free",
    ),
    "openai": ("gpt-5-mini", "gpt-5", "gpt-4.1-mini", "gpt-4o-mini"),
    "custom": (),
}


def preset_models(preset: str) -> list[AvailableModel]:
    """Curated models for a preset, labeled for display."""
    return [
        AvailableModel(id=model_id, label=model_label(model_id))
        for model_id in MODEL_CATALOG.get(preset, ())
    ]


class ModelSettingsUpdate(ModelConfig):
    api_key: str | None = None


class ModelListProbe(WireModel):
    """Unsaved settings-form values used to list a provider's models.

    Every field is optional: an empty probe lists the currently saved provider.
    """

    base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=200)
    api_key: str | None = Field(default=None, max_length=2_000)


class ModelListView(WireModel):
    models: list[AvailableModel] = Field(default_factory=list)


class ModelSetupSkip(WireModel):
    """Records that the user dismissed first-run model setup.

    Persisted so the dialog does not reappear on every launch; configuring a
    model later clears the flag so setup is never shown again.
    """

    skipped: bool = False


class ModelSettingsView(ModelConfig):
    pass


class ConnectionBindingView(WireModel):
    configured: bool = False
    verified: bool = False
    label: str | None = None


class YuqueApiBindingView(ConnectionBindingView):
    active: bool = False


class ConnectionTestResult(WireModel):
    connected: bool
    message: str
    label: str | None = None


class YuqueApiSettingsUpdate(WireModel):
    token: str | None = Field(default=None, max_length=2_000)


class FeishuBindingUpdate(WireModel):
    webhook_url: str | None = Field(default=None, max_length=2_000)


class ModelPresetCapabilities(WireModel):
    """What a preset supports, so the UI can offer only valid choices."""

    reasoning_levels: list[str] = Field(default_factory=list)
    default_reasoning_effort: str = ""


class SettingsView(WireModel):
    model: ModelSettingsView
    has_api_key: bool
    data_path: str
    screenshot_count: int
    web_search: WebSearchSettings
    runtime: RuntimeSettingsInput | None = None
    yuque_api: YuqueApiBindingView = Field(default_factory=YuqueApiBindingView)
    feishu: ConnectionBindingView = Field(default_factory=ConnectionBindingView)
    # Curated per-preset catalogue so the model picker has choices on first
    # paint, before an API key is entered and before any live query.
    model_presets: dict[str, list[AvailableModel]] = Field(default_factory=dict)
    # Per-preset reasoning support, keyed like `model_presets`.
    model_capabilities: dict[str, ModelPresetCapabilities] = Field(default_factory=dict)
    # True once the user dismissed first-run setup, so it is not shown again.
    model_setup_skipped: bool = False


class WebSearchSettingsUpdate(WireModel):
    mode: Literal["off", "ask", "auto"]
    max_results: int = Field(ge=1, le=10)
    query_rewrite: bool = True
    searxng_url: str = ""
    api_key: str | None = None


__all__ = [
    "MODEL_CATALOG",
    "MODEL_PRESETS",
    "AvailableModel",
    "ConnectionBindingView",
    "ConnectionTestResult",
    "FeishuBindingUpdate",
    "ModelConfig",
    "ModelConnectionResult",
    "ModelListProbe",
    "ModelListView",
    "ModelPresetCapabilities",
    "ModelSettingsUpdate",
    "ModelSettingsView",
    "ModelSetupSkip",
    "SettingsView",
    "WebSearchSettingsUpdate",
    "YuqueApiBindingView",
    "YuqueApiSettingsUpdate",
    "model_label",
    "preset_models",
]
