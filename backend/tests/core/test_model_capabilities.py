"""厂商**模型级**参数能力表的判据测试。

这些用例是跨厂商兼容性的防线：改错字段名、把某厂商的规则套到同厂商其他模型上，
或漏掉钳制，都会让真实请求 400 —— 而这类失败只在用户切模型时才暴露。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.model_capabilities import (
    REASONING_EFFORT_LABELS,
    REASONING_EFFORT_VALUES,
    TEMPERATURE_MIN_DETERMINISTIC,
    accepts_temperature,
    clamp_temperature,
    default_reasoning_effort,
    reasoning_levels,
    reasoning_params,
    supports_reasoning_control,
)
from app.schemas.settings import (
    FREE_ONLY_PRESETS,
    MODEL_CATALOG,
    MODEL_PRESETS,
    is_free_model,
    preset_models,
)

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
# 官方（platform.moonshot.cn/docs/guide/use-thinking-effort 与 use-thinking-models）：
# K3 始终推理、用顶层 reasoning_effort（low/high/max，**默认 max**），不可关闭；
# K2.6 / K2.5 只有 thinking.type 的 enabled/disabled 且**不支持** reasoning_effort；
# K2.7-code 固定 enabled，连开关都没有。
def test_kimi_k3_uses_reasoning_effort_and_cannot_be_turned_off() -> None:
    assert reasoning_levels("kimi", "kimi-k3") == ("low", "high", "max")
    # 官方默认是 max，不是 high —— 早期版本填错成 high，会静默少算一档推理。
    assert default_reasoning_effort("kimi", "kimi-k3") == "max"
    for level in reasoning_levels("kimi", "kimi-k3"):
        assert reasoning_params("kimi", "kimi-k3", level) == {"reasoning_effort": level}
    assert reasoning_params("kimi", "kimi-k3", "off") == {}


def test_kimi_k2_uses_thinking_type_instead_of_reasoning_effort() -> None:
    """同厂商不同模型字段不同 —— 这正是必须按模型而非按厂商判定的原因。"""
    assert reasoning_levels("kimi", "kimi-k2.6") == ("off", "on")
    assert reasoning_params("kimi", "kimi-k2.6", "off") == {"thinking": {"type": "disabled"}}
    assert reasoning_params("kimi", "kimi-k2.6", "on") == {"thinking": {"type": "enabled"}}
    # 官方明确 K2.6 不支持 reasoning_effort，传 high 必须什么都不发。
    assert reasoning_params("kimi", "kimi-k2.6", "high") == {}


def test_kimi_k27_code_thinking_is_forced_on() -> None:
    assert reasoning_levels("kimi", "kimi-k2.7-code") == ()
    assert reasoning_params("kimi", "kimi-k2.7-code", "off") == {}


# --------------------------------------------------------------- DeepSeek
# 官方（api-docs.deepseek.com/guides/thinking_mode）：开关是 thinking.type
# （默认 enabled），effort 直传 low/high/max（medium→high、xhigh→high）。
# 所以「开思考 + 高强度」应该发 reasoning_effort，而不是 thinking。
def test_deepseek_uses_thinking_type_for_the_toggle() -> None:
    assert reasoning_levels("deepseek", "deepseek-flash") == ("off", "low", "high", "max")
    assert reasoning_params("deepseek", "deepseek-flash", "off") == {"thinking": {"type": "disabled"}}
    assert reasoning_params("deepseek", "deepseek-flash", "high") == {"reasoning_effort": "high"}
    assert reasoning_params("deepseek", "deepseek-flash", "max") == {"reasoning_effort": "max"}
    assert default_reasoning_effort("deepseek", "deepseek-flash") == "high"


# ----------------------------------------------------------------- 智谱
# 官方（docs.bigmodel.cn 核心参数页 + docs.z.ai/guides/llm/glm-5.3）：
# reasoning_effort 仅 GLM-5.2+ 支持；5.3 只能 enabled 且只有 low/high/max
# （**默认 max**）；5.2 支持七档；4.7 强制思考；4.6/4.5 只有开关。
def test_glm_4_6_has_no_reasoning_effort_and_only_exposes_the_toggle() -> None:
    assert reasoning_levels("glm", "glm-4.6") == ("off", "on")
    assert reasoning_params("glm", "glm-4.6", "off") == {"thinking": {"type": "disabled"}}
    assert reasoning_params("glm", "glm-4.6", "high") == {}


def test_glm_5_3_cannot_disable_thinking_but_scales_effort() -> None:
    # 官方是 low / high / max 三档，没有 medium；默认 max。
    assert reasoning_levels("glm", "glm-5.3") == ("low", "high", "max")
    assert default_reasoning_effort("glm", "glm-5.3") == "max"
    assert reasoning_params("glm", "glm-5.3", "off") == {}
    assert reasoning_params("glm", "glm-5.3", "high") == {"reasoning_effort": "high"}
    assert reasoning_params("glm", "glm-5.3", "max") == {"reasoning_effort": "max"}
    # 5.3 没有 medium 档，传了必须被丢弃而不是原样透传。
    assert reasoning_params("glm", "glm-5.3", "medium") == {}


def test_glm_5_2_exposes_all_seven_documented_efforts() -> None:
    """7 档全暴露。早期把它压成 off/low/high 三档，medium/xhigh/none 全部丢失。"""
    assert reasoning_levels("glm", "glm-5.2") == (
        "none",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    )
    for level in reasoning_levels("glm", "glm-5.2"):
        assert reasoning_params("glm", "glm-5.2", level) == {"reasoning_effort": level}
    assert default_reasoning_effort("glm", "glm-5.2") == "max"


# ----------------------------------------------------------------- OpenAI
# 官方（platform.openai.com/docs/guides/latest-model + Azure reasoning 文档）：
# 顶层 reasoning_effort 直传。GPT-6 Astra / 6.1 Sol **没有 none**；
# 6 Sol/Luna、5.6 家族、5.5 有 none 但没有 max（max 仅 Responses API）。
def test_openai_gpt6_astra_has_no_none_level() -> None:
    assert reasoning_levels("openai", "gpt-6-astra") == ("low", "medium", "high", "xhigh")
    assert reasoning_params("openai", "gpt-6-astra", "none") == {}
    assert reasoning_params("openai", "gpt-6-astra", "xhigh") == {"reasoning_effort": "xhigh"}


def test_openai_gpt56_family_has_none_but_no_max() -> None:
    for model in ("gpt-5.6-terra", "gpt-5.6-sol", "gpt-5.6-luna"):
        assert reasoning_levels("openai", model) == (
            "none",
            "low",
            "medium",
            "high",
            "xhigh",
        ), model
        # max 只在 Responses API 可用，Chat Completions 传了会 400。
        assert reasoning_params("openai", model, "max") == {}


def test_openai_gpt54_family_has_no_none_level() -> None:
    assert reasoning_levels("openai", "gpt-5.4") == ("low", "medium", "high", "xhigh")
    assert reasoning_params("openai", "gpt-5.4", "none") == {}


def test_openai_non_reasoning_models_declare_no_levels() -> None:
    """4.1 / 4o 官方枚举里没有 reasoning_effort，传了会 400。"""
    for model in ("gpt-4.1", "gpt-4.1-mini", "gpt-4o-mini"):
        assert reasoning_levels("openai", model) == (), model
        assert reasoning_params("openai", model, "high") == {}


# ------------------------------------------------------- temperature 约束
def test_openai_reasoning_family_rejects_temperature() -> None:
    """带上非默认 temperature 会 400，与 reasoning 无关。

    gpt-5.5: "Unsupported parameter: 'temperature'"；5.6 家族只接受默认值 1；
    GPT-6 Astra 要求整段省略。
    """
    for model in ("gpt-5.5", "gpt-5.6-terra", "gpt-5.6-sol", "gpt-6-astra", "gpt-6-luna"):
        assert not accepts_temperature("openai", model), model


def test_openai_5_4_and_below_still_accept_temperature() -> None:
    for model in ("gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano"):
        assert accepts_temperature("openai", model), model


def test_kimi_and_glm_temperature_policy() -> None:
    # K2.x 会把 temperature 固定成 1.0，传别的值直接报错。
    assert not accepts_temperature("kimi", "kimi-k3")
    assert not accepts_temperature("kimi", "kimi-k2.6")
    # 智谱接受 temperature，只是区间收窄到 [0, 1]（由 clamp_temperature 处理）。
    assert accepts_temperature("glm", "glm-5.3")
    assert accepts_temperature("glm", "glm-4.6")
    assert accepts_temperature("deepseek", "deepseek-flash")
    # 未登记厂商保持旧行为。
    assert accepts_temperature("custom", "some-model")
    assert accepts_temperature("qwen", "qwen3.8-max")


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


# ------------------------------------------------------------- 目录表一致性
def test_preset_defaults_match_catalogue_heads() -> None:
    """MODEL_PRESETS 的默认模型必须是 MODEL_CATALOG 的第一项。

    两者无类型关联（一个是 tuple，一个是 UI 展示列表），各自独立漂移会让
    「切换预设后表单里的模型」与「能力表登记的模型」对不上。
    """
    for preset, (_base_url, model) in MODEL_PRESETS.items():
        catalog = MODEL_CATALOG.get(preset, ())
        if not catalog:
            assert model == "", f"{preset} 没有目录却指定了默认模型 {model}"
            continue
        assert model == catalog[0], f"{preset} 默认 {model} 与目录首项 {catalog[0]} 不一致"


def test_catalogue_has_no_duplicate_ids_per_preset() -> None:
    for preset, catalog in MODEL_CATALOG.items():
        assert len(set(catalog)) == len(catalog), f"{preset} 目录里有重复 id"


# ------------------------------------------------------------- 免费档限制
def test_free_only_presets_expose_nothing_but_free_models() -> None:
    """Zen 是按量计费网关，非免费 id 会直接扣余额，目录里绝不能出现。"""
    for preset in FREE_ONLY_PRESETS:
        catalog = MODEL_CATALOG[preset]
        assert catalog, f"{preset} 被限制为免费档却没有可用模型"
        for model in catalog:
            assert is_free_model(preset, model), f"{preset} 目录混入付费模型 {model}"


def test_free_only_preset_default_is_free() -> None:
    for preset in FREE_ONLY_PRESETS:
        assert is_free_model(preset, MODEL_PRESETS[preset][1])


def test_is_free_model_only_restricts_free_only_presets() -> None:
    """订阅制与普通厂商不受影响，否则会误杀付费模型。"""
    for preset in MODEL_PRESETS:
        if preset in FREE_ONLY_PRESETS:
            continue
        assert is_free_model(preset, "gpt-5.6-terra")
        assert is_free_model(preset, "kimi-k3")


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        ("mimo-v2.6-flash-free", True),
        ("deepseek-v4-flash-free", True),
        # Big Pickle 没有 -free 后缀，但官方定价表里它是 Free。
        ("big-pickle", True),
        ("big-pickle-free", True),
        ("MiMo-V2.6-Flash-Free", True),
        ("kimi-k3", False),
        ("gpt-5.6-terra", False),
        ("mimo-v2.6-flash", False),
        ("", False),
    ],
)
def test_is_free_model_classification(model_id: str, expected: bool) -> None:
    assert is_free_model("opencode_zen", model_id) is expected


def test_preset_models_filters_paid_ids_for_free_only_presets() -> None:
    ids = [model.id for model in preset_models("opencode_zen")]
    assert ids
    assert all(is_free_model("opencode_zen", model_id) for model_id in ids)
    # 已从 Zen 目录移除的付费档不能再出现。
    assert "kimi-k3" not in ids
    assert "glm-5.3-flash" not in ids


def test_preset_models_drops_a_paid_id_that_leaks_into_the_catalogue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """目录本身也必须过筛，不能只靠「录入时保证干净」。

    上面那条断言在当前数据下即使删掉过滤也会通过，所以这里故意注入一个
    付费 id，锁住 `preset_models` 里那层过滤真的生效。
    """
    monkeypatch.setitem(
        MODEL_CATALOG,
        "opencode_zen",
        ("mimo-v2.6-flash-free", "kimi-k3", "big-pickle"),
    )

    assert [model.id for model in preset_models("opencode_zen")] == [
        "mimo-v2.6-flash-free",
        "big-pickle",
    ]


def test_unrestricted_preset_still_lists_paid_models() -> None:
    assert "gpt-5.6-terra" in [model.id for model in preset_models("openai")]
    assert "kimi-k3" in [model.id for model in preset_models("kimi")]


def test_zen_catalogue_matches_live_snapshot() -> None:
    """目录里的 Zen 免费档必须与线上 `/models` 的免费集合完全一致。

    免费档会随官方活动增删，所以这里用一份抓取时的快照（tests/fixtures/
    opencode_zen_models.json）当判据：多收或漏收都会让离线用户看到一份
    已经下线、或看不到新上线的清单。快照过期时刷新它，并顺手核对
    opencode.ai/docs/zen 的定价表。
    """
    snapshot = json.loads(
        (Path(__file__).resolve().parents[1] / "fixtures" / "opencode_zen_models.json").read_text(
            encoding="utf-8"
        )
    )
    live = {entry["id"] for entry in snapshot["data"]}
    live_free = {model for model in live if is_free_model("opencode_zen", model)}

    assert set(MODEL_CATALOG["opencode_zen"]) == live_free, (
        f"目录与快照不一致：多收 {sorted(set(MODEL_CATALOG['opencode_zen']) - live_free)}，"
        f"漏收 {sorted(live_free - set(MODEL_CATALOG['opencode_zen']))}"
    )
