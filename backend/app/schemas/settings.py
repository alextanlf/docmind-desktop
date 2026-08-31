from __future__ import annotations

from pydantic import Field

from app.core.llm import ModelConfig, ModelConnectionResult
from app.schemas.common import WireModel

MODEL_PRESETS = {
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat"),
    "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    "openai": ("https://api.openai.com/v1", "gpt-5-mini"),
    "custom": ("", ""),
}


class ModelSettingsUpdate(ModelConfig):
    api_key: str | None = Field(default=None, max_length=1024)


class ModelSettingsView(ModelConfig):
    pass


class SettingsView(WireModel):
    model: ModelSettingsView
    has_api_key: bool
    data_path: str
    screenshot_count: int


__all__ = [
    "MODEL_PRESETS",
    "ModelConfig",
    "ModelConnectionResult",
    "ModelSettingsUpdate",
    "ModelSettingsView",
    "SettingsView",
]
