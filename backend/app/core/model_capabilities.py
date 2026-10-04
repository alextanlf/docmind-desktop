"""云端厂商的**模型级**请求参数能力表。

**必须按模型而非按厂商判定。** 同一家厂商的不同模型参数不通用，官方文档实例：

- Kimi `kimi-k3` 用顶层 `reasoning_effort`（low/high/max，**始终推理不可关**），
  而 `kimi-k2.6` / `kimi-k2.7-code` 用 `thinking.type`，且 K2.7-code **固定 enabled 不可关**。
- 智谱 `reasoning_effort` **仅 GLM-5.2 及以上支持**；GLM-5.3 只能 enabled，
  GLM-4.7 / 4.5V 强制思考，GLM-4.6 / 4.5 由模型自动判断。
- DeepSeek 思考开关是 `thinking.type`（`enabled`/`disabled`，**默认 enabled**），
  `reasoning_effort` 只在 thinking 开启时生效，值 high/max（low、medium 映射为 high，
  xhigh 映射为 max）。

**temperature 区间也随厂商不同**：DeepSeek 是 `[0, 2]`，智谱是 `[0, 1]`
且明确写 `do_sample=False (temperature=0) 在 OpenAI 调用中并不适用`。

**未登记的模型一律不下发任何厂商专属字段** —— 走 OpenAI 基础字段
（model/messages/temperature/stream）。这是唯一安全的默认：猜错字段名会直接 400。

来源：各家官方 API 文档（api-docs.deepseek.com 的 thinking_mode 与 pricing 页、
platform.moonshot.cn/docs/api/chat、docs.z.ai/api-reference/llm/chat-completion、
platform.xiaomimimo.com 文档与下线公告、opencode.ai/docs/zen 与 /docs/go）。
"""

from __future__ import annotations

from typing import Literal

# 统一档位语义。各家字段名与允许值不同，用户看到的含义一致。
ReasoningEffort = Literal["off", "low", "medium", "high"]

REASONING_EFFORT_VALUES: tuple[str, ...] = ("off", "low", "medium", "high")

REASONING_EFFORT_LABELS: dict[str, str] = {
    "off": "关闭思考",
    "low": "轻量",
    "medium": "标准",
    "high": "深度",
}

# 每个条目的字段：
#   style:   "effort" 用 reasoning_effort | "thinking" 用 thinking.type | "none" 不下发
#   levels:  用户可选档位（官方实际接受的子集）
#   default: 该模型的官方默认档位
_NO_CONTROL: dict[str, object] = {"style": "none", "levels": (), "default": ""}

# ---------------------------------------------------------------- DeepSeek
# 官方 thinking_mode 页：thinking.type 默认 enabled；reasoning_effort 取 high/max，
# low/medium → high，xhigh → max。thinking 模式下 temperature 等采样参数无效（不报错但无效）。
# 现行模型名是 deepseek-flash 与 deepseek-v4-pro；旧的 chat/reasoner 已下线但仍被接受。
_DEEPSEEK = {"style": "thinking", "levels": ("off", "low", "high"), "default": "high"}

# ---------------------------------------------------------------- Kimi
# 官方 chat 页：K3 始终推理，用 reasoning_effort low/high/max，不可关；
# K2.6 与 K2.7-code 用 thinking.type，K2.7-code 固定 enabled 不可关。
_KIMI_MODELS = {
    "kimi-k3": {"style": "effort", "levels": ("low", "high"), "default": "high"},
    "kimi-k2.7-code": {"style": "thinking", "levels": (), "default": ""},
    "kimi-k2.6": {"style": "thinking", "levels": ("off", "high"), "default": "high"},
    "kimi-k2.5": {"style": "thinking", "levels": ("off", "high"), "default": "high"},
}

# ---------------------------------------------------------------- 智谱 GLM
# 官方 chat-completion 页：thinking.type 默认 enabled。GLM-5.3/5.3-FLASH 只能 enabled，
# 深度由 reasoning_effort 控制（该字段仅 5.2+ 支持）；4.7 强制思考；
# 4.6/4.5 自动判断。4.6 及以下没有 reasoning_effort，故只暴露开关两档。
_GLM_MODELS = {
    "glm-5.3": {"style": "effort", "levels": ("low", "high"), "default": "high"},
    "glm-5.3-flash": {"style": "effort", "levels": ("low", "high"), "default": "high"},
    "glm-5.2": {"style": "effort", "levels": ("off", "low", "high"), "default": "high"},
    "glm-5.1": {"style": "effort", "levels": ("off", "low", "high"), "default": "high"},
    "glm-5": {"style": "effort", "levels": ("off", "low", "high"), "default": "high"},
    # 强制思考且无 reasoning_effort，只能 enabled。
    "glm-4.7": {"style": "thinking", "levels": (), "default": ""},
    "glm-4.6": {"style": "thinking", "levels": ("off", "high"), "default": "high"},
    "glm-4.5": {"style": "thinking", "levels": ("off", "high"), "default": "high"},
}

# ---------------------------------------------------------------- 小米 MiMo
# 官方使用 thinking 开关。V2.5 系列已于 2026-10-21 下线，预设默认给 V2.6。
_MIMO = {"style": "thinking", "levels": ("off", "high"), "default": "high"}

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
}

# 聚合网关后端厂商不固定；通义/未核对厂商同样保守处理 —— 不下发推理字段。
_PRESET_FALLBACK: dict[str, dict[str, object]] = {
    "qwen": _NO_CONTROL,
    "openai": _NO_CONTROL,
    "custom": _NO_CONTROL,
    "opencode_zen": _NO_CONTROL,
    "opencode_go": _NO_CONTROL,
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


def clamp_temperature(value: float) -> float:
    """把 temperature 钳制到所有在册厂商都接受的区间。

    存在的唯一理由：改写类任务想要确定性（temperature=0），但智谱明确拒绝 0。
    落到 0.1 兼顾"足够确定"与"各家合法"。
    """
    if value <= 0:
        return TEMPERATURE_MIN_DETERMINISTIC
    return min(value, TEMPERATURE_MAX)


def reasoning_params(preset: str, model: str, effort: str) -> dict[str, object]:
    """把统一档位翻译成该模型认识的请求字段。

    未登记模型、或档位不在其支持列表时返回空 dict —— 调用方据此**不追加任何字段**，
    避免给厂商发送它不认识的参数而触发 400。
    """
    profile = _profile(preset, model)
    if effort not in tuple(profile.get("levels") or ()):
        return {}
    style = profile.get("style")
    if style == "effort":
        return {"reasoning_effort": effort}
    if style == "thinking":
        return {"thinking": {"type": "enabled" if effort != "off" else "disabled"}}
    return {}
