from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.core.llm import AvailableModel, ModelConfig, ModelConnectionResult, model_label
from app.schemas.common import WireModel
from app.schemas.ollama import RuntimeSettingsInput
from app.schemas.web_search import WebSearchSettings

MODEL_PRESETS = {
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat"),
    "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    "kimi": ("https://api.moonshot.cn/v1", "kimi-k2.5"),
    "glm": ("https://open.bigmodel.cn/api/paas/v4", "glm-4.6"),
    "mimo": ("https://api.xiaomimimo.com/v1", "mimo-v2.5-pro"),
    "openai": ("https://api.openai.com/v1", "gpt-5-mini"),
    "custom": ("", ""),
}

# Curated per-preset model catalogue. Lets the settings form offer a real choice
# before an API key exists (and when the provider has no `/models` endpoint),
# while `GET /models` still overrides this with the provider's live list.
# Verified against vendor docs; the first entry is the preset default.
MODEL_CATALOG: dict[str, tuple[str, ...]] = {
    "deepseek": ("deepseek-chat", "deepseek-reasoner"),
    "qwen": ("qwen-plus", "qwen-max", "qwen-turbo", "qwen-flash"),
    "kimi": ("kimi-k2.5", "kimi-k2", "kimi-latest", "moonshot-v1-128k", "moonshot-v1-32k"),
    "glm": ("glm-4.6", "glm-4.5", "glm-4-plus", "glm-4-flash"),
    "mimo": ("mimo-v2.5-pro", "mimo-v2.5", "mimo-v2.5-flash"),
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
    "ModelSettingsUpdate",
    "ModelSettingsView",
    "SettingsView",
    "WebSearchSettingsUpdate",
    "YuqueApiBindingView",
    "YuqueApiSettingsUpdate",
    "model_label",
    "preset_models",
]
