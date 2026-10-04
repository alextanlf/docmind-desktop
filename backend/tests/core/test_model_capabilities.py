"""厂商参数能力表的判据测试。

这些用例是跨厂商兼容性的防线：改错字段名或漏掉钳制都会让真实请求 400，
而这类失败只有在用户切换厂商时才会暴露。
"""

from __future__ import annotations

import pytest

from app.core.model_capabilities import (
    REASONING_EFFORT_VALUES,
    TEMPERATURE_MIN_DETERMINISTIC,
    clamp_temperature,
    default_reasoning_effort,
    reasoning_levels,
    reasoning_params,
    supports_reasoning_control,
)
from app.schemas.settings import MODEL_PRESETS


def test_every_preset_default_effort_is_within_its_own_levels() -> None:
    """默认档位必须是自己支持的档位之一，否则会静默失效。"""
    for preset in MODEL_PRESETS:
        if not supports_reasoning_control(preset):
            continue
        levels = reasoning_levels(preset)
        default = default_reasoning_effort(preset)
        assert default in levels, f"{preset} 默认 {default!r} 不在 {levels} 中"


def test_every_level_maps_to_a_non_empty_payload() -> None:
    """每个可选档位都要能翻译成真实字段，不能静默变成空。"""
    for preset in MODEL_PRESETS:
        for level in reasoning_levels(preset):
            assert reasoning_params(preset, level), f"{preset}/{level} 未翻译"


def test_unknown_preset_and_level_never_emit_vendor_fields() -> None:
    """未登记厂商 / 非法档位 -> 不发任何厂商专属字段，避免 400。"""
    assert reasoning_params("some-unknown-vendor", "high") == {}
    assert reasoning_params("custom", "high") == {}
    assert reasoning_params("kimi", "nonexistent-level") == {}
    assert reasoning_params("kimi", "") == {}


def test_kimi_uses_reasoning_effort_and_never_sends_off() -> None:
    """Kimi K3 始终推理、不可关闭，所以档位里没有 off。"""
    assert "off" not in reasoning_levels("kimi")
    assert reasoning_params("kimi", "low") == {"reasoning_effort": "low"}
    assert reasoning_params("kimi", "high") == {"reasoning_effort": "high"}


def test_deepseek_uses_none_to_disable_thinking() -> None:
    """DeepSeek 官方文档：reasoning_effort 默认 high，none 表示关闭思考。"""
    assert default_reasoning_effort("deepseek") == "high"
    assert reasoning_params("deepseek", "off") == {"reasoning_effort": "none"}
    assert reasoning_params("deepseek", "high") == {"reasoning_effort": "high"}


def test_glm_and_mimo_use_thinking_type_not_reasoning_effort() -> None:
    """字段名不通用：给智谱/小米发 reasoning_effort 是错的。"""
    for preset in ("glm", "mimo"):
        assert reasoning_params(preset, "off") == {"thinking": {"type": "disabled"}}
        assert reasoning_params(preset, "high") == {"thinking": {"type": "enabled"}}
        assert "reasoning_effort" not in reasoning_params(preset, "high")


def test_opencode_gateways_declare_no_reasoning_control() -> None:
    """OpenCode 是聚合网关，后端厂商不固定，不下发推理字段最安全。"""
    for preset in ("opencode_zen", "opencode_go"):
        assert not supports_reasoning_control(preset)
        assert reasoning_params(preset, "high") == {}


def test_no_vendor_receives_a_both_shapes_reasoning_payload() -> None:
    """同时出现两个字段名会让部分厂商直接 400。"""
    for preset in MODEL_PRESETS:
        for level in reasoning_levels(preset):
            params = reasoning_params(preset, level)
            has_effort = "reasoning_effort" in params
            has_thinking = "thinking" in params
            assert not (has_effort and has_thinking), f"{preset} 同时下发两种字段"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 0 is exactly what Zhipu rejects, so it must never reach the wire.
        (0, TEMPERATURE_MIN_DETERMINISTIC),
        (0.0, TEMPERATURE_MIN_DETERMINISTIC),
        (-1, TEMPERATURE_MIN_DETERMINISTIC),
        # Above Zhipu's ceiling of 1.
        (2, 1.0),
        (1.5, 1.0),
        # Already legal values pass through untouched.
        (0.2, 0.2),
        (0.1, 0.1),
        (1.0, 1.0),
    ],
)
def test_clamp_temperature_keeps_every_vendor_happy(raw: float, expected: float) -> None:
    assert clamp_temperature(raw) == expected


def test_clamped_temperature_is_accepted_by_zhipus_documented_range() -> None:
    """智谱区间是 (0, 1]，钳制结果必须落在开区间内。"""
    for raw in (0, 0.2, 1, 2, 99):
        value = clamp_temperature(raw)
        assert 0 < value <= 1


def test_every_reasoning_value_label_is_declared() -> None:
    from app.core.model_capabilities import REASONING_EFFORT_LABELS

    assert set(REASONING_EFFORT_VALUES) == set(REASONING_EFFORT_LABELS)


def test_opencode_base_urls_match_official_docs() -> None:
    """端点经实测：GET /models 无需 key 即返回 200。"""
    assert MODEL_PRESETS["opencode_zen"][0] == "https://opencode.ai/zen/v1"
    assert MODEL_PRESETS["opencode_go"][0] == "https://opencode.ai/zen/go/v1"
