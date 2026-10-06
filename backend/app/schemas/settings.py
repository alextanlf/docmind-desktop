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
    "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen3.8-max"),
    "kimi": ("https://api.moonshot.cn/v1", "kimi-k3"),
    "glm": ("https://open.bigmodel.cn/api/paas/v4", "glm-4.6"),
    # MiMo-V2.5 is retired on 2026-10-21 per the vendor's deprecation notice.
    "mimo": ("https://api.xiaomimimo.com/v1", "mimo-v2.6-pro"),
    # OpenCode Zen is pay-as-you-go with a rotating set of free models; Go is the
    # flat monthly plan. Both verified live: GET /models returns 200 without a key.
    # Zen is free-only (see FREE_ONLY_PRESETS), so its default must be a free id.
    "opencode_zen": ("https://opencode.ai/zen/v1", "mimo-v2.6-flash-free"),
    "opencode_go": ("https://opencode.ai/zen/go/v1", "mimo-v2.6-pro"),
    "openai": ("https://api.openai.com/v1", "gpt-5.6-terra"),
    "custom": ("", ""),
}

# Presets that must only ever expose zero-cost models. OpenCode Zen is a paid
# gateway that also serves a rotating free tier; picking a paid id there bills
# the user's balance without any explicit confirmation, so the picker is
# restricted to the free tier. Go is a flat subscription, so it is unrestricted.
FREE_ONLY_PRESETS: frozenset[str] = frozenset({"opencode_zen"})

# Free ids all end in "-free" except Big Pickle, a stealth model OpenCode bills
# at zero during its evaluation window (opencode.ai/docs/zen pricing table).
_FREE_MODEL_ALIASES: frozenset[str] = frozenset({"big-pickle"})


def is_free_model(preset: str, model_id: str) -> bool:
    """Whether a model id is allowed for a preset.

    Only meaningful for FREE_ONLY_PRESETS; every other preset accepts anything.
    """
    if preset not in FREE_ONLY_PRESETS:
        return True
    normalized = model_id.strip().lower()
    return normalized.endswith("-free") or normalized in _FREE_MODEL_ALIASES

# Curated per-preset model catalogue. Lets the settings form offer a real choice
# before an API key exists (and when the provider has no `/models` endpoint),
# while `GET /models` still overrides this with the provider's live list.
# Ids verified against each vendor's official docs and, for OpenCode, against a
# live `GET /models` call. The first entry is the preset default and must match
# MODEL_PRESETS; `test_preset_defaults_match_catalogue_heads` enforces that.
MODEL_CATALOG: dict[str, tuple[str, ...]] = {
    "deepseek": ("deepseek-flash", "deepseek-v4-pro"),
    # help.aliyun.com/zh/document_detail/2867560.html — the Qwen3.5+ generational
    # ids replaced the old qwen-plus/max/turbo tier names. DashScope's
    # /models needs a key (401 without one), so this list is the only source
    # the picker has before the user configures one.
    "qwen": (
        "qwen3.8-max",
        "qwen3.8-flash",
        "qwen3.7-plus",
        "qwen3.7-flash",
        "qwen3.6-plus",
        "qwen3.5-plus",
        "qwen3.5-flash",
        "qwen-max",
        "qwen-turbo",
    ),
    "kimi": ("kimi-k3", "kimi-k2.6", "kimi-k2.5", "kimi-k2.7-code"),
    "glm": ("glm-4.6", "glm-5.2", "glm-5.3", "glm-5.3-flash", "glm-4.7"),
    "mimo": ("mimo-v2.6-pro", "mimo-v2.6-flash"),
    # Zen free tier, from a live `GET https://opencode.ai/zen/v1/models`
    # (200 without a key, 86 models, 14 free once Big Pickle is counted) plus
    # the pricing table on opencode.ai/docs/zen. Paid ids are excluded on
    # purpose — see FREE_ONLY_PRESETS. This tier rotates without notice, so the
    # live list is what the picker trusts once a key exists; this curated copy
    # is what it falls back to. `test_zen_catalogue_matches_live_snapshot` keeps
    # the two in sync.
    "opencode_zen": (
        "mimo-v2.6-flash-free",
        "deepseek-v4-flash-free",
        "nemotron-3-ultra-free",
        "nemotron-3.5-lightning-free",
        "big-pickle",
        "space-bunny-free",
        "longcat-2.5-preview-free",
        "fledge-alpha-free",
        "ling-3.1-flash-free",
        "ling-3.0-flash-fin-free",
        "mimo-v2.5-free",
        "jev-1.13-free",
        "muse-spark-1.3-contributor-free",
        "muse-spark-1.2-contributor-free",
    ),
    "opencode_go": (
        "mimo-v2.6-pro",
        "mimo-v2.5",
        "mimo-v2.5-pro",
        "deepseek-v4-flash",
        "deepseek-v4-pro",
        "glm-5.3-flash",
        "kimi-k2.6",
        "longcat-2.0",
    ),
    # platform.openai.com/docs/models — GPT-6 / GPT-5.6 are the current
    # families; gpt-5-mini and gpt-5.1 are listed Deprecated upstream.
    "openai": (
        "gpt-5.6-terra",
        "gpt-6-astra",
        "gpt-5.6-sol",
        "gpt-5.6-luna",
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4o-mini",
    ),
    "custom": (),
}


def preset_models(preset: str) -> list[AvailableModel]:
    """Curated models for a preset, labeled for display."""
    return [
        AvailableModel(id=model_id, label=model_label(model_id))
        for model_id in MODEL_CATALOG.get(preset, ())
        if is_free_model(preset, model_id)
    ]


class ModelSettingsUpdate(ModelConfig):
    api_key: str | None = None


class ModelListProbe(WireModel):
    """Unsaved settings-form values used to list a provider's models.

    Every field is optional: an empty probe lists the currently saved provider.

    `preset` must be included. Without it the service can only fall back to the
    *saved* preset's curated catalogue, so a user picking a vendor they have
    never saved sees an empty list and the form reports "该服务商未返回模型
    列表" — the picker is driven by the form, not by what happens to be stored.
    """

    preset: str | None = Field(default=None, max_length=32)
    base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=200)
    api_key: str | None = Field(default=None, max_length=2_000)


class ModelListView(WireModel):
    """Result of a model listing.

    `source` and `notice` exist because a "curated" fallback must not be
    reported to the user as if it came from the provider: the form used to say
    "已获取 N 个可用模型" for a hardcoded list, and "该服务商未返回模型列表"
    when the real problem was an unset API key or a 401.
    """

    models: list[AvailableModel] = Field(default_factory=list)
    # "live" = the provider answered; "curated" = fell back to the built-in list.
    source: Literal["live", "curated"] = "live"
    # Why the fallback happened, in user-facing Chinese. None on success.
    notice: str | None = None


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
    # Reasoning support keyed by preset, then by model id — the same vendor's
    # models differ (Kimi K3 vs K2.6, GLM-5.3 vs 4.6). The nesting must match
    # what SettingsService.view() builds: declaring the inner value as a bare
    # ModelPresetCapabilities makes pydantic silently drop every model's entry
    # and emit default-empty capabilities under each preset.
    model_capabilities: dict[str, dict[str, ModelPresetCapabilities]] = Field(
        default_factory=dict
    )
    # True once the user dismissed first-run setup, so it is not shown again.
    model_setup_skipped: bool = False


class WebSearchSettingsUpdate(WireModel):
    mode: Literal["off", "ask", "auto"]
    max_results: int = Field(ge=1, le=10)
    query_rewrite: bool = True
    searxng_url: str = ""
    api_key: str | None = None


__all__ = [
    "FREE_ONLY_PRESETS",
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
    "is_free_model",
    "preset_models",
]
