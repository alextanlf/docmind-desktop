"""云端厂商的**模型级**请求参数能力表。

**必须按模型而非按厂商判定。** 同一家厂商的不同模型参数不通用，官方文档实例：

- Kimi `kimi-k3` 用顶层 `reasoning_effort`（low/high/max，**始终推理不可关**），
  而 `kimi-k2.6` / `kimi-k2.5` 用 `thinking.type` 的开/关两档，K2.7-code 固定
  enabled 连开关都没有。K2.6 **不支持** reasoning_effort。
- 智谱 `reasoning_effort` **仅 GLM-5.2 及以上支持**。GLM-5.3 只有 low/high/max
  三档、不可关；GLM-5.2 支持 none/minimal/low/medium/high/xhigh/max 七档；
  GLM-4.7 强制思考且无 reasoning_effort；GLM-4.6 / 4.5 只有 thinking 开/关。
- DeepSeek 思考开关是 `thinking.type`（默认 enabled），`reasoning_effort`
  直传 low/high/max，medium 与 xhigh 会被映射成 high、ultra 映射成 max。
- OpenAI 顶层 `reasoning_effort` 直传，各模型档位不同：GPT-6 Astra / 6.1 Sol
  只有 low/medium/high/xhigh（**无 none**），GPT-6 Sol/Luna 与 5.6/5.5 有
  none/low/medium/high/xhigh，GPT-5.4/5.2/5.1 只有 low/medium/high/xhigh。

**档位值就是厂商原生的字符串，不做抽象层。** 早期版本把它压成
off/low/medium/high 四档并配中文标签，结果是 5.3 的 max、5.2 的七档、OpenAI 的
none/xhigh 全部丢失，且默认值填错（K3/5.3 官方默认是 max）。用户看到的档位
必须等于厂商文档里的档位，否则「深度」在不同厂商之间根本不是一回事。

**temperature 不是无条件下发的。** OpenAI 推理家族会拒绝它：gpt-5.5 明确
`Unsupported parameter: 'temperature'`，5.6 家族「accepts only the default value 1」，
GPT-6 Astra 要求整段省略。带上它就是 400，与 reasoning 无关。
未登记 temperature 能力的模型走 `temperature` 缺省（不下发该字段）。

**未登记的模型一律不下发任何厂商专属字段** —— 走 OpenAI 基础字段
（model/messages/stream）。这是唯一安全的默认：猜错字段名会直接 400。

来源：各家官方 API 文档（api-docs.deepseek.com/guides/thinking_mode、
platform.moonshot.cn/docs/guide/use-thinking-models 与 use-thinking-effort、
docs.bigmodel.cn/cn/guide/start/concept-param/ 与 docs.z.ai/guides/llm/glm-5.3、
platform.xiaomimimo.com/docs/usage-guide/passing-back-reasoning_content、
platform.openai.com/docs/guides/latest-model 与 learn.microsoft.com/azure/ai-services/
openai/how-to/reasoning、opencode.ai/docs/zen）。
"""

from __future__ import annotations

from typing import Literal

# 档位标识符 = 厂商原生的 reasoning_effort 值，或 thinking 型厂商的 "off"/"on"。
# 前端直接展示这些值，不再套一层自造的中文语义。
ReasoningEffort = str

# 出现在 UI 里的全部门位值。用于契约测试与文档，不是可选项集合 ——
# 每个模型只暴露自己官方支持的那几个（见 _MODEL_CAPABILITIES）。
REASONING_EFFORT_VALUES: tuple[str, ...] = (
    "off",
    "on",
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)

REASONING_EFFORT_LABELS: dict[str, str] = {
    "off": "关闭思考",
    "on": "开启思考",
    "none": "不推理",
    "minimal": "最低",
    "low": "低",
    "medium": "中",
    "high": "高",
    "xhigh": "很高",
    "max": "最高",
}

# 每个条目的字段：
#   style:   "effort" 用 reasoning_effort | "thinking" 用 thinking.type | "none" 不下发
#   levels:  该模型官方接受的档位（原生值）
#   default: 该模型的官方默认档位；空串表示不下发字段、走厂商默认
#   temp:    "omit" = 带上 temperature 会 400，必须整段省略
_NO_CONTROL: dict[str, object] = {"style": "none", "levels": (), "default": "", "temp": "allow"}

# ---------------------------------------------------------------- DeepSeek
# api-docs.deepseek.com/guides/thinking_mode：thinking.type 默认 enabled；
# reasoning_effort 直传 low/high/max（medium→high、xhigh→high、ultra→max）。
# 直传原生值即可，不需要自己再做映射，所以 low/high/max 三档都给出。
# 额外提供 off：DeepSeek 允许 thinking.type=disabled 完全关思考，此时不发
# reasoning_effort（官方语义是 effort 只在 thinking 开启时生效）。
# temperature 在思考模式下无效（不报错），故不特殊处理。
_DEEPSEEK = {
    "style": "effort",
    "levels": ("off", "low", "high", "max"),
    "default": "high",
    "temp": "allow",
}

# ---------------------------------------------------------------- Kimi
# platform.moonshot.cn/docs/guide/use-thinking-models 的字段对照表：
#   kimi-k3        reasoning_effort low/high/max（默认 max），thinking 仅 enabled
#   kimi-k2.7-code 固定 enabled，无 reasoning_effort
#   kimi-k2.6      thinking.type enabled/disabled，不支持 reasoning_effort
#   kimi-k2.5      同 K2.6
_KIMI_MODELS = {
    # 始终推理、不可关，故没有 off/on 两档。
    "kimi-k3": {"style": "effort", "levels": ("low", "high", "max"), "default": "max", "temp": "omit"},
    # 固定 enabled：连开关都没有，等于无档位可选。
    "kimi-k2.7-code": {"style": "none", "levels": (), "default": "", "temp": "omit"},
    "kimi-k2.6": {"style": "thinking", "levels": ("off", "on"), "default": "on", "temp": "omit"},
    "kimi-k2.5": {"style": "thinking", "levels": ("off", "on"), "default": "on", "temp": "omit"},
}

# ---------------------------------------------------------------- 智谱 GLM
# docs.bigmodel.cn/cn/guide/start/concept-param/ 与 docs.z.ai/guides/llm/glm-5.3：
#   GLM-5.3 / 5.3-FLASH  强制 enabled，reasoning_effort 仅 low/high/max（默认 max）
#   GLM-5.2              reasoning_effort 支持 none/minimal/low/medium/high/xhigh/max
#   GLM-5.1 / GLM-5      reasoning_effort 同上族，默认 max
#   GLM-4.7              强制思考、无 reasoning_effort → 无档位
#   GLM-4.6 / GLM-4.5    只有 thinking.type 自动判断，即开/关两档
# temperature 区间 [0, 1] 且拒绝 0，由 clamp_temperature 统一处理。
_GLM_MODELS = {
    "glm-5.3": {"style": "effort", "levels": ("low", "high", "max"), "default": "max", "temp": "allow"},
    "glm-5.3-flash": {
        "style": "effort",
        "levels": ("low", "high", "max"),
        "default": "max",
        "temp": "allow",
    },
    # 七档全暴露：none/minimal 会让模型放弃思考，medium 才是默认值。
    "glm-5.2": {
        "style": "effort",
        "levels": ("none", "minimal", "low", "medium", "high", "xhigh", "max"),
        "default": "max",
        "temp": "allow",
    },
    "glm-5.1": {
        "style": "effort",
        "levels": ("none", "minimal", "low", "medium", "high", "xhigh", "max"),
        "default": "max",
        "temp": "allow",
    },
    "glm-5": {
        "style": "effort",
        "levels": ("none", "minimal", "low", "medium", "high", "xhigh", "max"),
        "default": "max",
        "temp": "allow",
    },
    "glm-4.7": {"style": "none", "levels": (), "default": "", "temp": "allow"},
    "glm-4.6": {"style": "thinking", "levels": ("off", "on"), "default": "on", "temp": "allow"},
    "glm-4.5": {"style": "thinking", "levels": ("off", "on"), "default": "on", "temp": "allow"},
}

# ---------------------------------------------------------------- 小米 MiMo
# platform.xiaomimimo.com：只有 thinking.type 的 enabled/disabled 两档，
# 无 reasoning_effort。V2.5 系列已于 2026-10-21 下线，预设默认给 V2.6。
_MIMO = {"style": "thinking", "levels": ("off", "on"), "default": "on", "temp": "allow"}

# ---------------------------------------------------------------- OpenAI
# platform.openai.com/docs/guides/latest-model 与 Azure 的 reasoning 文档给出
# 各模型的 reasoning_effort 枚举。注意 GPT-6 Astra / 6.1 Sol **没有 none**
# （Azure 标为 including none，但 EvoLink/OpenAI 的枚举表明确列出无 none，
# 取更保守的一份：多给 none 的风险是 400，少给只是少一个选项）。
# 5.5 与 5.6 家族拒绝非默认 temperature，5.4/5.2/5.1 接受。
_OPENAI_REASONING = {
    "gpt-6-astra": {"style": "effort", "levels": ("low", "medium", "high", "xhigh"), "default": "medium", "temp": "omit"},
    "gpt-6.1-sol": {"style": "effort", "levels": ("low", "medium", "high", "xhigh"), "default": "medium", "temp": "omit"},
    "gpt-6-sol": {"style": "effort", "levels": ("none", "low", "medium", "high", "xhigh"), "default": "medium", "temp": "omit"},
    "gpt-6-luna": {"style": "effort", "levels": ("none", "low", "medium", "high", "xhigh"), "default": "medium", "temp": "omit"},
    "gpt-5.6-sol": {"style": "effort", "levels": ("none", "low", "medium", "high", "xhigh"), "default": "medium", "temp": "omit"},
    "gpt-5.6-terra": {"style": "effort", "levels": ("none", "low", "medium", "high", "xhigh"), "default": "medium", "temp": "omit"},
    "gpt-5.6-luna": {"style": "effort", "levels": ("none", "low", "medium", "high", "xhigh"), "default": "medium", "temp": "omit"},
    "gpt-5.5": {"style": "effort", "levels": ("none", "low", "medium", "high", "xhigh"), "default": "low", "temp": "omit"},
    "gpt-5.4": {"style": "effort", "levels": ("low", "medium", "high", "xhigh"), "default": "medium", "temp": "allow"},
    "gpt-5.4-mini": {"style": "effort", "levels": ("low", "medium", "high", "xhigh"), "default": "medium", "temp": "allow"},
    "gpt-5.4-nano": {"style": "effort", "levels": ("low", "medium", "high", "xhigh"), "default": "medium", "temp": "allow"},
    # 4.1 / 4o 是非推理模型，官方枚举里没有 reasoning_effort —— 不登记。
}

_MODEL_CAPABILITIES: dict[tuple[str, str], dict[str, object]] = {
    **{
        ("deepseek", name): dict(_DEEPSEEK)
        for name in ("deepseek-flash", "deepseek-v4-pro", "deepseek-chat", "deepseek-reasoner")
    },
    **{("kimi", name): dict(caps) for name, caps in _KIMI_MODELS.items()},
    **{("glm", name): dict(caps) for name, caps in _GLM_MODELS.items()},
    **{
        ("mimo", name): dict(_MIMO)
        for name in ("mimo-v2.6-pro", "mimo-v2.6-flash", "mimo-v2.5", "mimo-v2.5-pro")
    },
    **{("openai", name): dict(caps) for name, caps in _OPENAI_REASONING.items()},
}

# 聚合网关后端厂商不固定；通义未核对文档同样保守处理 —— 不下发推理字段。
_PRESET_FALLBACK: dict[str, dict[str, object]] = {
    "qwen": dict(_NO_CONTROL),
    "custom": dict(_NO_CONTROL),
    "opencode_zen": dict(_NO_CONTROL),
    "opencode_go": dict(_NO_CONTROL),
}

# temperature 的合法区间与"确定性"下限。
# DeepSeek 是 [0, 2]（允许 0），智谱是 [0, 1]（拒绝 0），取交集。
TEMPERATURE_MAX = 1.0
TEMPERATURE_MIN_DETERMINISTIC = 0.1


def _profile(preset: str, model: str) -> dict[str, object]:
    key = (preset, (model or "").strip().lower())
    found = _MODEL_CAPABILITIES.get(key)
    if found is not None:
        return found
    return _PRESET_FALLBACK.get(preset, _NO_CONTROL)


def reasoning_levels(preset: str, model: str) -> tuple[str, ...]:
    """该「厂商 + 模型」组合支持的用户可选档位；空元组表示不支持调节。"""
    return tuple(_profile(preset, model).get("levels") or ())


def default_reasoning_effort(preset: str, model: str) -> str:
    """官方默认档位；空串表示不追加厂商专属字段。"""
    return str(_profile(preset, model).get("default") or "")


def supports_reasoning_control(preset: str, model: str) -> bool:
    return bool(reasoning_levels(preset, model))


def accepts_temperature(preset: str, model: str) -> bool:
    """该模型是否接受 temperature 字段。

    False 时调用方**必须整段省略** temperature —— OpenAI 推理家族会直接 400
    （`Unsupported parameter: 'temperature'` / 只接受默认值 1）。
    """
    return str(_profile(preset, model).get("temp", "allow")) != "omit"


def clamp_temperature(value: float) -> float:
    """把 temperature 钳制到所有在册厂商都接受的区间。

    存在的唯一理由：改写类任务想要确定性（temperature=0），但智谱明确拒绝 0。
    落到 0.1 兼顾"足够确定"与"各家合法"。
    """
    if value <= 0:
        return TEMPERATURE_MIN_DETERMINISTIC
    return min(value, TEMPERATURE_MAX)


def reasoning_params(preset: str, model: str, effort: str) -> dict[str, object]:
    """把档位翻译成该模型认识的请求字段。

    档位值即厂商原生值，所以 effort 型直接透传。唯一的例外是 `off`：
    DeepSeek 允许彻底关思考，而它关思考用的是 `thinking.type` 而非
    `reasoning_effort`（官方语义：effort 只在 thinking 开启时生效），
    所以 off 走 thinking 分支且不附带 effort 字段。

    未登记模型、或档位不在其支持列表时返回空 dict —— 调用方据此**不追加任何字段**，
    避免给厂商发送它不认识的参数而触发 400。
    """
    profile = _profile(preset, model)
    if effort not in tuple(profile.get("levels") or ()):
        return {}
    if effort == "off":
        # 只有 DeepSeek 登记了 off；thinking 型厂商（Kimi/GLM-4.x/MiMo）的
        # off 同样映射到 thinking.type=disabled，两条路径在此合流。
        return {"thinking": {"type": "disabled"}}
    style = profile.get("style")
    if style == "effort":
        return {"reasoning_effort": effort}
    if style == "thinking":
        return {"thinking": {"type": "enabled"}}
    return {}
