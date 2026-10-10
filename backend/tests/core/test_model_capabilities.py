"""厂商**模型级**参数能力表的判据测试。

这些用例是跨厂商兼容性的防线：改错字段名、把某厂商的规则套到同厂商其他模型上，
或漏掉钳制，都会让真实请求 400 —— 而这类失败只在用户切模型时才暴露。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.core.llm import model_label
from app.core.model_capabilities import (
    REASONING_EFFORT_LABELS,
    REASONING_EFFORT_VALUES,
    TEMPERATURE_MIN_DETERMINISTIC,
    accepts_temperature,
    capability_basis,
    clamp_temperature,
    default_reasoning_effort,
    reasoning_levels,
    reasoning_params,
    registered_models,
    supports_reasoning_control,
)
from app.schemas.settings import (
    FREE_ONLY_PRESETS,
    MODEL_CATALOG,
    MODEL_CATALOG_DOC_SOURCES,
    MODEL_CATALOG_KEYLESS_MODELS_ENDPOINTS,
    MODEL_CATALOG_MAX_AGE_DAYS,
    MODEL_CATALOG_MAX_AGE_DAYS_BY_PRESET,
    MODEL_CATALOG_REVIEWED_AT,
    MODEL_PRESETS,
    RETIRED_MODELS,
    catalog_max_age_days,
    catalog_review_today,
    is_free_model,
    is_retired_model,
    preset_models,
    stale_catalogs,
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
    assert reasoning_params("deepseek", "deepseek-flash", "off") == {
        "thinking": {"type": "disabled"}
    }
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

    判据取自 `RETIRED_MODELS`，按厂商分组查，所以聚合网关不需要特殊豁免：
    OpenCode 至今仍在提供 mimo-v2.5，而小米官方的下线公告只登记在
    ``RETIRED_MODELS["mimo"]`` 下，`RETIRED_MODELS["opencode_go"]` 是空的。
    旧版本用一张全局集合 + `gateways` 跳过表来表达同一件事，那等于在下游手工
    维护"谁不受约束"，加一家网关就会漏。
    """
    for preset, (_base_url, model) in MODEL_PRESETS.items():
        assert not is_retired_model(preset, model), f"{preset} 默认模型 {model} 已下线"


def test_catalogue_never_lists_a_retired_model() -> None:
    """目录里绝不能出现已下线的模型 —— 这是"读过下线公告"的可执行形式。

    用户选中一个已下线的 id 之后才知道它调不通，而这一步本来只需要有人读过
    厂商公告。公告读到了就必须落到 `RETIRED_MODELS`：只读不记，这条断言不会
    生效，目录也照样能收进那个死 id。
    """
    for preset, catalog in MODEL_CATALOG.items():
        retired = RETIRED_MODELS.get(preset, frozenset())
        leaked = sorted(set(catalog) & retired)
        assert not leaked, f"{preset} 目录里混入已下线模型 {leaked}"


# 下线的 id 分两类，必须逐个归类，不能靠"厂商有没有能力表"推断。
#
# 判据是**这个 id 当初登记过没有**，而不是**它的厂商有没有登记过别的模型**：
# openai 预设登记了 gpt-5.x/6.x，但 gpt-5-mini 从来没有登记过，用厂商粒度判断
# 会得出"openai 有档位，所以 gpt-5-mini 也该有"这种错误结论。
_RETIRED_IDS_WITH_PROFILES: frozenset[tuple[str, str]] = frozenset(
    {
        ("deepseek", "deepseek-chat"),
        ("deepseek", "deepseek-reasoner"),
        ("mimo", "mimo-v2.5"),
        ("mimo", "mimo-v2.5-pro"),
    }
)

# ⚠️ 已知缺口，不是"没问题"：这三个 id 下线时能力表里就没有它们，于是老配置会落
# 进 `_NO_CONTROL` —— 不发档位，temperature 照发。对 gpt-5-mini / gpt-5.1 来说
# 这大概率是错的（OpenAI 推理家族拒绝非默认 temperature，会 400），但补登记需要
# 先查到官方参数，不能靠猜字段值。列在这里是为了让缺口**可见且被追踪**：将来
# 补登记时把它移到上面那组即可，归类断言会强制这个动作。
_RETIRED_IDS_WITHOUT_PROFILES: frozenset[tuple[str, str]] = frozenset(
    {
        ("qwen", "qwen-plus"),
        ("openai", "gpt-5-mini"),
        ("openai", "gpt-5.1"),
    }
)


def test_every_retired_id_is_classified() -> None:
    """登记进 RETIRED_MODELS 的每个 id，都要在上述两组里归好类。

    没有这条，"新读到一个下线公告 → 加一行 RETIRED_MODELS"这个动作就只完成了
    一半：能力表认不认得它、老配置会不会静默降级，全凭运气。
    """
    declared = _RETIRED_IDS_WITH_PROFILES | _RETIRED_IDS_WITHOUT_PROFILES
    actual = {(preset, model) for preset, models in RETIRED_MODELS.items() for model in models}
    assert declared == actual, (
        f"未归类：{sorted(actual - declared)}；已失效：{sorted(declared - actual)}"
    )


def test_retired_ids_with_profiles_keep_them() -> None:
    """有档位的下线 id 必须继续解析出档位。

    删掉这些登记不会报错、不会 400，只会让老配置里的思考档位静默消失 ——
    用户没有任何提示。所以这里把它们逐个钉住。
    """
    for preset, model in sorted(_RETIRED_IDS_WITH_PROFILES):
        assert (preset, model) in registered_models(), f"{preset}/{model} 不再被能力表认得"
        assert reasoning_levels(preset, model), f"{preset}/{model} 的档位登记被删空了"


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


def test_every_catalogued_preset_declares_its_capability_basis() -> None:
    """每家上了目录的厂商，都必须**明确**声明它的能力数据从哪来。

    缺省路径（`_profile` 落进 `_NO_CONTROL`）是静默成功的：不发厂商专属字段、
    不报 400，界面上只是少了几个档位。于是"新增一家厂商时忘了做能力决定"这个
    错误没有任何症状 —— 除非有人去逐行读能力表。这条断言把那个隐形状态变成
    构建失败，逼着新增厂商的人二选一：逐个登记模型，或显式声明整家走兜底。
    """
    for preset in MODEL_CATALOG:
        assert capability_basis(preset) != "implicit", (
            f"{preset} 已在 MODEL_CATALOG 里，却既没有模型级能力条目，"
            f"也没有写进 _PRESET_FALLBACK —— 能力数据落在了隐式缺省上"
        )


def test_every_registered_model_has_a_display_label() -> None:
    """登记了参数能力的模型，必须在显示名表里有名字。

    能力表是逐个模型核对官方文档的产物；显示名表是用户实际看到的东西。只在能力
    表里登记、显示名表里没有，意味着这份核对结果只生效了一半：模型选择框里会把
    原始 id 直接摆给用户，而同厂商其他模型都是可读的名字。

    反向不成立，也不该断言："能力表里的模型都在目录里"会逼着人把 deepseek-chat
    这类历史 id 误标成在售 —— 见 `registered_models` 的说明。
    """
    unlabeled = sorted(
        f"{preset}/{model}" for preset, model in registered_models() if model_label(model) == model
    )
    assert not unlabeled, f"这些模型登记了能力却没有显示名：{unlabeled}"


# ------------------------------------------------------------- 目录陈旧度闸门
def test_every_catalogued_preset_records_when_and_where_to_review() -> None:
    """每份要核对的目录，都要同时有"什么时候核对的"和"去哪核对"。

    两张表必须逐键对齐：只有日期没有来源，闸门响的时候不知道该看哪一页；只有来源
    没有日期，闸门永远不会响。`custom` 两边都没有 —— 它没有厂商目录可核对。
    """
    catalogued = {preset for preset, models in MODEL_CATALOG.items() if models}
    assert set(MODEL_CATALOG_REVIEWED_AT) == catalogued
    assert set(MODEL_CATALOG_DOC_SOURCES) == catalogued


def test_doc_sources_are_public_pages_not_the_api_endpoint() -> None:
    """来源必须是公开文档页，**不能**是厂商自己的 API 端点。

    DocMind 装在用户机子上，key 只在用户自己的钥匙串里。把厂商 `/models` 这类需要
    凭据的地址写成"核对来源"，等于把闸门接到一条要求凭据、而凭据不能离开用户机器
    的路径上 —— 这条断言把那个错误挡在数据层。
    """
    for preset, sources in MODEL_CATALOG_DOC_SOURCES.items():
        assert sources, f"{preset} 没有登记任何核对来源"
        base_url = MODEL_PRESETS[preset][0]
        for url in sources:
            assert url.startswith("https://"), f"{preset} 的来源不是 https：{url}"
            assert not url.startswith(base_url), f"{preset} 的来源是它自己的 API 端点：{url}"


def test_review_dates_are_iso_and_not_in_the_future() -> None:
    """日期要能被解析，且不能是未来。

    容忍超前 1 天：口径是 UTC（见 `catalog_review_today`），而人贴日期看的是自己
    桌上的日历 —— 东半球的工作日会领先 UTC 一天。这条断言要抓的是 `2062-10-04`
    这类笔误，不是时区差。
    """
    today = catalog_review_today()
    for preset, reviewed in MODEL_CATALOG_REVIEWED_AT.items():
        try:
            parsed = date.fromisoformat(reviewed)
        except ValueError:
            pytest.fail(f"{preset} 的核对日期 {reviewed!r} 不是 YYYY-MM-DD")
        assert parsed <= today + timedelta(days=1), f"{preset} 的核对日期 {reviewed} 在未来"


def test_no_catalogue_is_past_its_review_deadline() -> None:
    """闸门本体：超过核对周期的目录会让构建失败。

    厂商不会通知我们它昨天上了什么新模型，所以"有没有人去看过"是唯一能离线检查
    的东西。没有这条，目录会一直停在某次改动的那天，而用户看到的是一个已经改名或
    下线的 id —— 且往往是在**没有 key 的首次设置**里看到，那时 live `/models`
    还没法兜底。

    到期时该做什么、不该做什么写在失败信息里 —— 这条断言响的时候，读它的人正是
    需要那两句话的人。
    """
    stale = stale_catalogs()
    assert not stale, (
        "这些预设的模型目录已超过核对周期，可能已经包含改名/下线的 id：\n"
        + "\n".join(
            f"  - {preset}: 最后核对 {reviewed}，已过 {age} 天（上限 {limit} 天）"
            f"\n      → {', '.join(MODEL_CATALOG_DOC_SOURCES[preset])}"
            for preset, reviewed, age, limit in stale
        )
        + "\n\n请对照上面的**公开文档页**核对 MODEL_CATALOG（改完把"
        + "\nMODEL_CATALOG_REVIEWED_AT 贴到当天），或重抓免 key 的 /models 端点。"
        + "\n⚠️ 不要用用户机子上的 API key 去做这件事 —— key 不能离开用户机器。"
    )


def test_the_staleness_gate_actually_fires() -> None:
    """守卫自身的守卫：闸门必须真的会响。

    否则它可能就是一段永远返回空列表的装饰 —— 而"永远绿"和"没问题"在读数的
    人眼里一模一样。这里把时钟推到所有周期之外，要求它把每一家都报出来。
    """
    worst_limit = max([MODEL_CATALOG_MAX_AGE_DAYS, *MODEL_CATALOG_MAX_AGE_DAYS_BY_PRESET.values()])
    far_future = catalog_review_today() + timedelta(days=worst_limit + 1)
    reported = {preset for preset, *_rest in stale_catalogs(today=far_future)}
    assert reported == set(MODEL_CATALOG_REVIEWED_AT)


def test_the_rotating_pools_review_window_is_tighter_in_effect() -> None:
    """Zen 的周期必须真的更短，而且这个"更短"要体现在判定上，不能只是个数。"""
    zen_limit = catalog_max_age_days("opencode_zen")
    assert zen_limit < MODEL_CATALOG_MAX_AGE_DAYS

    reviewed = date.fromisoformat(MODEL_CATALOG_REVIEWED_AT["opencode_zen"])
    just_past_zen = reviewed + timedelta(days=zen_limit + 1)
    stale = {preset for preset, *_rest in stale_catalogs(today=just_past_zen)}
    assert "opencode_zen" in stale, "紧周期没有生效"
    # 同一天按默认周期算它还不该到期 —— 否则"周期更短"就是句空话。
    assert just_past_zen <= reviewed + timedelta(days=MODEL_CATALOG_MAX_AGE_DAYS)


def test_keyless_endpoints_are_only_declared_for_known_presets() -> None:
    """免 key 端点表不能指向不存在的预设，也不能冒充文档页。"""
    for preset, endpoint in MODEL_CATALOG_KEYLESS_MODELS_ENDPOINTS.items():
        assert preset in MODEL_CATALOG, f"{preset} 不在目录里"
        assert endpoint.startswith("https://"), f"{preset} 的端点不是 https：{endpoint}"
        assert endpoint not in MODEL_CATALOG_DOC_SOURCES.get(preset, ()), (
            f"{preset} 把机器抓取的端点写进了需要人读的文档页里"
        )


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
