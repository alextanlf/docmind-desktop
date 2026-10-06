"""Runtime settings for local (on-device) inference.

Local inference servers all speak the OpenAI-compatible dialect, so the only
per-server variation is where it listens and which model to run. Ollama
(11434), LM Studio (1234) and any other server on a loopback port are therefore
the same configuration with different values — the vendor name is deliberately
absent from every field name so no caller branches on it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.common import WireModel

# Every loopback port is allowed: Ollama listens on 11434, LM Studio on 1234,
# llama.cpp's server on 8080. The port is a *server* setting, not a vendor one.
DEFAULT_LOCAL_BASE_URL = "http://127.0.0.1:11434"
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class LocalModelConfig(WireModel):
    base_url: str = DEFAULT_LOCAL_BASE_URL
    model: str = Field(default="", max_length=200)
    # Local servers ignore auth by default, but LM Studio can require a token.
    # Kept out of the URL so it never lands in a log line or a probe message.
    api_key: str = Field(default="", max_length=500)
    timeout_seconds: float = Field(default=120, gt=0, le=600)

    @field_validator("base_url")
    @classmethod
    def safe_base_url(cls, value: str) -> str:
        if (
            not isinstance(value, str)
            or value != value.strip()
            or any(char.isspace() for char in value)
        ):
            raise ValueError("invalid local model URL")
        try:
            parsed = urlsplit(value)
        except ValueError as error:
            raise ValueError("invalid local model URL") from error
        if (
            parsed.scheme.lower() != "http"
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path.rstrip("/")
        ):
            raise ValueError("invalid local model URL")
        if parsed.hostname is None or parsed.hostname.lower() not in _LOOPBACK_HOSTS:
            raise ValueError("invalid local model URL")
        return value.rstrip("/")

    @field_validator("model")
    @classmethod
    def valid_model(cls, value: str) -> str:
        if any(ord(char) < 32 or ord(char) == 127 or char.isspace() for char in value):
            raise ValueError("invalid model tag")
        return value


class RoutingSettings(WireModel):
    mode: Literal["local_only", "cloud_only", "automatic"] = "cloud_only"


class RagSettings(WireModel):
    """Retrieval and memory-recall limits surfaced in the settings UI.

    ``max_sources`` caps how many chunks reach the LLM; raising it improves
    recall on global questions ("summarize everything") at linear prompt cost.
    ``memory_recall_min_similarity`` only gates memory recall — document
    retrieval intentionally has no similarity gate, because a fixed absolute
    cosine threshold was measured to reject legitimate short queries.
    """

    max_sources: int = Field(default=5, ge=1, le=20)
    memory_recall_min_similarity: float = Field(default=0.65, ge=-1.0, le=1.0)


class RuntimeSettingsInput(WireModel):
    local: LocalModelConfig = LocalModelConfig()
    routing: RoutingSettings = RoutingSettings()
    rag: RagSettings = RagSettings()


class GenerationRoute(WireModel):
    source: Literal["local", "cloud"]
    model: str = Field(min_length=1, max_length=200)
    mode: Literal["local_only", "cloud_only", "automatic"]
    fallback_reason: str | None = Field(default=None, max_length=128)


class LocalStatusView(WireModel):
    available: bool
    base_url: str
    version: str | None = None
    selected_model: str = ""
    selected_model_available: bool = False
    checked_at: datetime
    message: str


class LocalModelView(WireModel):
    id: str
    label: str


class LocalModelsView(WireModel):
    available: bool
    models: list[LocalModelView] = Field(default_factory=list)
    checked_at: datetime
    message: str


def validate_model_tag(value: str) -> str:
    if (
        not value
        or len(value) > 200
        or any(ord(char) < 32 or ord(char) == 127 or char.isspace() for char in value)
    ):
        raise ValueError("invalid model tag")
    return value


__all__ = [
    "DEFAULT_LOCAL_BASE_URL",
    "UUID",
    "GenerationRoute",
    "LocalModelConfig",
    "LocalModelView",
    "LocalModelsView",
    "LocalStatusView",
    "RagSettings",
    "RoutingSettings",
    "RuntimeSettingsInput",
    "validate_model_tag",
]
