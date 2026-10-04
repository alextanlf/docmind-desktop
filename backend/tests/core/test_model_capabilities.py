"""厂商**模型级**参数能力表的判据测试。

这些用例是跨厂商兼容性的防线：改错字段名、把某厂商的规则套到同厂商其他模型上，
或漏掉钳制，都会让真实请求 400 —— 而这类失败只在用户切模型时才暴露。
"""

from __future__ import annotations

import pytest

from app.core.model_capabilities import (
    REASONING_EFFORT_LABELS,
    REASONING_EFFORT_VALUES,
    TEMPERATURE_MIN_DETERMINISTIC,
    clamp_temperature,
    default_reasoning_effort,
    reasoning_levels,
    reasoning_params,
    supports_reasoning_control,
)
from app.schemas.settings import MODEL_CATALOG, MODEL_PRESETS

# 遍历「预设里列出的每个模型」，确保没有漏登记或登记错。
_ALL_MODELS = [(preset, model) for preset, models in MODEL_CATALOG.items() for model in models]


def test_every_catalogued_model_default_is_within_its_own_levels() -> None:
    """默认档位必须是自己支持的档位之一，否则会静默失效。"""
    for preset, model in _ALL_MODELS:
        if not supports_reasoning_control(preset, model):
            continue
        levels = reasoning_levels(preset, model)
        default = default_reasoning_effort(preset, model)
        assert default in levels, f"{preset}/{model} 默认 {default!r} 不在 {levels} 中"


def test_every_level_maps_to_a_non_empty_payload() -> None:
    """每个可选档位都要能翻译成真实字段，不能静默变成空。"""
    for preset, model in _ALL_MODELS:
        for level in reasoning_levels(preset, model):
            assert reasoning_params(preset, model, level), f"{preset}/{model}/{level} 未翻译"


def test_no_vendor_receives_both_reasoning_field_shapes() -> None:
    """同时出现两个字段名会让部分厂商直接 400。"""
    for preset, model in _ALL_MODELS:
        for level in reasoning_levels(preset, model):
            params = reasoning_params(preset, model, level)
            assert not ("reasoning_effort" in params and "thinking" in params), (
                f"{preset}/{model} 同时下发两种字段"
            )


# ------------------------------------------------------------------ Kimi
# 官方：K3 始终推理、用 reasoning_effort（low/high/max），不可关闭；
# K2.6 / K2.7-code 用 thinking.type，且 K2.7-code 固定 enabled 不可关。
def test_kimi_k3_uses_reasoning_effort_and_cannot_be_turned_off() -> None:
    assert reasoning_levels("kimi", "kimi-k3") == ("low", "high")
    assert reasoning_params("kimi", "kimi-k3", "low") == {"reasoning_effort": "low"}
    assert reasoning_params("kimi", "kimi-k3", "off") == {}


def test_kimi_k2_uses_thinking_type_instead_of_reasoning_effort() -> None:
    """同厂商不同模型字段不同 —— 这正是必须按模型而非按厂商判定的原因。"""
    assert reasoning_params("kimi", "kimi-k2.6", "off") == {"thinking": {"type": "disabled"}}
    assert reasoning_params("kimi", "kimi-k2.6", "high") == {"thinking": {"type": "enabled"}}
    assert "reasoning_effort" not in reasoning_params("kimi", "kimi-k2.6", "high")


def test_kimi_k27_code_thinking_is_forced_on() -> None:
    assert reasoning_levels("kimi", "kimi-k2.7-code") == ()
    assert reasoning_params("kimi", "kimi-k2.7-code", "off") == {}


# --------------------------------------------------------------- DeepSeek
# 官方：思考开关是 thinking.type（默认 enabled），reasoning_effort 仅在思考开启时生效。
def test_deepseek_uses_thinking_type_for_the_toggle() -> None:
    assert reasoning_params("deepseek", "deepseek-flash", "off") == {"thinking": {"type": "disabled"}}
    assert reasoning_params("deepseek", "deepseek-flash", "high") == {"thinking": {"type": "enabled"}}
    assert default_reasoning_effort("deepseek", "deepseek-flash") == "high"


# ----------------------------------------------------------------- 智谱
# 官方：reasoning_effort 仅 GLM-5.2+ 支持；5.3 只能 enabled（深度靠 effort）；
# 4.7 强制思考；4.6/4.5 只有开关。
def test_glm_4_6_has_no_reasoning_effort_and_only_exposes_the_toggle() -> None:
    assert reasoning_levels("glm", "glm-4.6") == ("off", "high")
    assert reasoning_params("glm", "glm-4.6", "off") == {"thinking": {"type": "disabled"}}


def test_glm_5_3_cannot_disable_thinking_but_scales_effort() -> None:
    assert reasoning_levels("glm", "glm-5.3") == ("low", "high")
    assert reasoning_params("glm", "glm-5.3", "off") == {}
    assert reasoning_params("glm", "glm-5.3", "high") == {"reasoning_effort": "high"}


def test_glm_4_7_thinking_is_forced_on() -> None:
    assert reasoning_levels("glm", "glm-4.7") == ()
    assert reasoning_params("glm", "glm-4.7", "off") == {}


# ------------------------------------------------------------------ 保守默认
def test_unregistered_model_and_bad_level_never_emit_vendor_fields() -> None:
    """未登记模型 / 非法档位 -> 不发任何厂商专属字段，避免 400。"""
    assert reasoning_params("some-unknown-vendor", "whatever", "high") == {}
    assert reasoning_params("custom", "whatever", "high") == {}
    assert reasoning_params("kimi", "kimi-k3", "nonexistent-level") == {}
    assert reasoning_params("kimi", "kimi-k3", "") == {}
    # 已知厂商但未登记的模型 -> 同样回退到不下发。
    assert reasoning_params("kimi", "kimi-does-not-exist", "high") == {}


def test_gateways_declare_no_reasoning_control() -> None:
    """OpenCode 是聚合网关，后端厂商不固定，不下发推理字段最安全。"""
    for preset in ("opencode_zen", "opencode_go"):
        assert not supports_reasoning_control(preset, "mimo-v2.5-free")
        assert reasoning_params(preset, "mimo-v2.5-free", "high") == {}


def test_unverified_vendors_stay_conservative() -> None:
    """通义 / OpenAI 未在本模块核对官方文档，保持不下发。"""
    assert not supports_reasoning_control("qwen", "qwen-plus")
    assert not supports_reasoning_control("openai", "gpt-5-mini")


# ------------------------------------------------------------- temperature
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0, TEMPERATURE_MIN_DETERMINISTIC),
        (0.0, TEMPERATURE_MIN_DETERMINISTIC),
        (-1, TEMPERATURE_MIN_DETERMINISTIC),
        (2, 1.0),
        (1.5, 1.0),
        (0.2, 0.2),
        (0.1, 0.1),
        (1.0, 1.0),
    ],
)
def test_clamp_temperature_keeps_every_vendor_happy(raw: float, expected: float) -> None:
    assert clamp_temperature(raw) == expected


def test_clamped_temperature_is_accepted_by_zhipus_documented_range() -> None:
    """智谱区间是 [0, 1] 且拒绝 0，钳制结果必须落在闭区间内且非零。"""
    for raw in (0, 0.2, 1, 2, 99):
        assert 0 < clamp_temperature(raw) <= 1


def test_every_reasoning_value_has_a_label() -> None:
    assert set(REASONING_EFFORT_VALUES) == set(REASONING_EFFORT_LABELS)


def test_no_preset_defaults_to_a_retired_model() -> None:
    """厂商已下线的模型不能作为**直连**预设的默认。

    仅适用于直连厂商：OpenCode 是聚合网关，它自己仍在提供 mimo-v2.5，
    该下线公告只约束小米官方端点。
    """
    direct_vendor_retired = {"deepseek-chat", "deepseek-reasoner", "mimo-v2.5", "mimo-v2.5-pro"}
    gateways = {"opencode_zen", "opencode_go"}
    for preset, (_base_url, model) in MODEL_PRESETS.items():
        if preset in gateways:
            continue
        assert model not in direct_vendor_retired, f"{preset} 默认模型 {model} 已下线"


def test_opencode_base_urls_match_official_docs() -> None:
    """端点经实测：GET /models 无需 key 即返回 200。"""
    assert MODEL_PRESETS["opencode_zen"][0] == "https://opencode.ai/zen/v1"
    assert MODEL_PRESETS["opencode_go"][0] == "https://opencode.ai/zen/go/v1"
