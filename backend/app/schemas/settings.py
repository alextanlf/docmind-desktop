from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.core.llm import ModelConfig, ModelConnectionResult
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


class ModelSettingsUpdate(ModelConfig):
    api_key: str | None = None


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


class WebSearchSettingsUpdate(WireModel):
    mode: Literal["off", "ask", "auto"]
    max_results: int = Field(ge=1, le=10)
    query_rewrite: bool = True
    searxng_url: str = ""
    api_key: str | None = None


__all__ = [
    "MODEL_PRESETS",
    "ConnectionBindingView",
    "ConnectionTestResult",
    "FeishuBindingUpdate",
    "ModelConfig",
    "ModelConnectionResult",
    "ModelSettingsUpdate",
    "ModelSettingsView",
    "SettingsView",
    "WebSearchSettingsUpdate",
    "YuqueApiBindingView",
    "YuqueApiSettingsUpdate",
]
