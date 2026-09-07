from __future__ import annotations

from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.common import WireModel


class OllamaConfig(WireModel):
    base_url: str = "http://127.0.0.1:11434"
    model: str = Field(default="", max_length=200)
    timeout_seconds: float = Field(default=120, gt=0, le=600)

    @field_validator("base_url")
    @classmethod
    def safe_base_url(cls, value: str) -> str:
        if not isinstance(value, str) or value != value.strip() or any(char.isspace() for char in value):
            raise ValueError("invalid Ollama URL")
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as error:
            raise ValueError("invalid Ollama URL") from error
        if parsed.scheme.lower() != "http" or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path.rstrip("/"):
            raise ValueError("invalid Ollama URL")
        if parsed.hostname is None or parsed.hostname.lower() not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("invalid Ollama URL")
        if port not in (None, 11434):
            raise ValueError("invalid Ollama URL")
        host = parsed.hostname.lower()
        rendered_host = f"[{host}]" if ":" in host else host
        return f"http://{rendered_host}:11434"

    @field_validator("model")
    @classmethod
    def valid_model(cls, value: str) -> str:
        if any(ord(char) < 32 or ord(char) == 127 or char.isspace() for char in value):
            raise ValueError("invalid model tag")
        return value


class RoutingSettings(WireModel):
    mode: Literal["local_only", "cloud_only", "automatic"] = "cloud_only"


class RuntimeSettingsInput(WireModel):
    ollama: OllamaConfig = OllamaConfig()
    routing: RoutingSettings = RoutingSettings()


class GenerationRoute(WireModel):
    source: Literal["local", "cloud"]
    model: str = Field(min_length=1, max_length=200)
    mode: Literal["local_only", "cloud_only", "automatic"]
    fallback_reason: str | None = Field(default=None, max_length=128)


class OllamaStatusView(WireModel):
    available: bool
    base_url: str
    version: str | None = None
    selected_model: str = ""
    selected_model_installed: bool = False
    checked_at: datetime
    message: str


class OllamaModelView(WireModel):
    name: str
    digest: str | None = None
    size_bytes: int | None = None
    modified_at: datetime | None = None
    family: str | None = None


class OllamaModelsView(WireModel):
    available: bool
    models: list[OllamaModelView] = Field(default_factory=list)
    checked_at: datetime
    message: str


class OllamaPullView(WireModel):
    id: UUID
    model_name: str
    base_url: str
    state: Literal["queued", "running", "completed", "failed", "cancelled"]
    progress: int = Field(ge=0, le=100)
    status: str | None = None
    total_bytes: int | None = None
    completed_bytes: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = True
    cancel_requested: bool = False
    last_event_sequence: int = Field(default=0, ge=0)
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime


def validate_model_tag(value: str) -> str:
    if not value or len(value) > 200 or any(ord(char) < 32 or ord(char) == 127 or char.isspace() for char in value):
        raise ValueError("invalid model tag")
    return value
