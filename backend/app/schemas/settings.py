from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.core.llm import ModelConfig, ModelConnectionResult
from app.schemas.common import WireModel
from app.schemas.web_search import WebSearchSettings

MODEL_PRESETS = {
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat"),
    "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    "openai": ("https://api.openai.com/v1", "gpt-5-mini"),
    "custom": ("", ""),
}


class ModelSettingsUpdate(ModelConfig):
    api_key: str | None = None


class ModelSettingsView(ModelConfig):
    pass


class SettingsView(WireModel):
    model: ModelSettingsView
    has_api_key: bool
    data_path: str
    screenshot_count: int
    web_search: WebSearchSettings


class WebSearchSettingsUpdate(WireModel):
    mode: Literal["off", "ask", "auto"]
    max_results: int = Field(ge=1, le=10)
    api_key: str | None = None


__all__ = [
    "MODEL_PRESETS",
    "ModelConfig",
    "ModelConnectionResult",
    "ModelSettingsUpdate",
    "ModelSettingsView",
    "SettingsView",
    "WebSearchSettingsUpdate",
]
