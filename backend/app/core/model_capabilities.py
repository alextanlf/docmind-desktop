"""云端厂商的请求参数能力表。

各家虽然都号称「OpenAI 兼容」，但**合法的参数名与取值范围并不一致**。
把差异集中在这里，而不是散落在 provider 与设置页里：

- `temperature` 区间：DeepSeek/通义是 `[0, 2]`，智谱是 `[0, 1]` 且**拒绝 0**
  （官方文档明确写 `do_sample = False (temperature = 0) 在 OpenAI 调用中并不适用`）。
  因此「想要确定性」不能一律发 0，必须落到各家共同接受的最小正值。
- 推理开关字段名**不通用**：Kimi/DeepSeek 用 `reasoning_effort`，
  智谱/小米用 `thinking.type`。给错字段可能直接 400，所以只对已知厂商下发。
- 思考内容一律走 `reasoning_content`，不作为正文。

**只发已确认支持的字段**：未登记的厂商一律不追加任何厂商专属参数，
走 OpenAI 基础字段（model/messages/temperature/stream），这是最保守的做法。

来源：各家官方 API 文档（DeepSeek api-docs.deepseek.com、Moonshot
platform.moonshot.cn/docs、智谱 docs.bigmodel.cn、小米 mimo.mi.com/docs、
opencode.ai/docs/zen 与 /docs/go）。表中未列出的厂商按"不支持"处理。
"""

from __future__ import annotations

from typing import Literal

# 统一的档位语义。各家字段名与允许值不同，但用户看到的档位含义一致。
ReasoningEffort = Literal["off", "low", "medium", "high"]

REASONING_EFFORT_VALUES: tuple[str, ...] = ("off", "low", "medium", "high")

# 档位在界面上的说明。默认值来自下表的 `default`，不使用各家厂商的默认。
REASONING_EFFORT_LABELS: dict[str, str] = {
    "off": "关闭思考",
    "low": "轻量",
    "medium": "标准",
    "high": "深度",
}

# 用 `reasoning_effort` 字段的厂商，取值按各家文档裁剪。
_REASONING_EFFORT_STYLE = {
    # Kimi：K3 始终推理、不可关闭，且只认 low/high/max；K2.x 另支持 thinking.type。
    # 因此不提供 off 档 —— 下发 `reasoning_effort: "off"` 是非法值。
    "kimi": {
        "style": "effort",
        "levels": ("low", "high"),
        "default": "low",
        "off_supported": False,
    },
    # DeepSeek：reasoning_effort 默认 high，none/low/high/max。
    "deepseek": {
        "style": "effort",
        "levels": ("off", "low", "high"),
        "default": "high",
        "off_supported": True,
    },
}

# 用 `thinking.type` 字段的厂商（智谱系与小米系同源）。
_THINKING_STYLE = {
    # 智谱：thinking.type 默认 enabled；GLM-4.6 由模型自动判断是否思考。
    "glm": {
        "style": "thinking",
        "levels": ("off", "low", "high"),
        "default": "high",
        "off_supported": True,
    },
    "mimo": {
        "style": "thinking",
        "levels": ("off", "low", "high"),
        "default": "high",
        "off_supported": True,
    },
    "qwen": {
        "style": "thinking",
        "levels": ("off", "low", "high"),
        "default": "off",
        "off_supported": True,
    },
    # OpenCode Zen / Go 是聚合网关，后端可能是任意厂商。
    # 官方文档未给统一推理参数，故不声明能力，保持保守默认。
    "opencode_zen": {"style": "none", "levels": (), "default": "", "off_supported": False},
    "opencode_go": {"style": "none", "levels": (), "default": "", "off_supported": False},
    "openai": {"style": "none", "levels": (), "default": "", "off_supported": False},
    "custom": {"style": "none", "levels": (), "default": "", "off_supported": False},
}

REASONING_PROFILES: dict[str, dict[str, object]] = {**_REASONING_EFFORT_STYLE, **_THINKING_STYLE}

# temperature 的合法区间与"确定性"下限。
# 智谱区间是 [0, 1] 且拒绝 0；DeepSeek/通义是 [0, 2] 且接受 0。
# 取所有在册厂商的交集作为钳制目标：0 < t <= 1。
TEMPERATURE_MAX = 1.0
TEMPERATURE_MIN_DETERMINISTIC = 0.1


def reasoning_levels(preset: str) -> tuple[str, ...]:
    """该预设支持的用户可选档位；空元组表示不支持调节。"""
    profile = REASONING_PROFILES.get(preset)
    if profile is None:
        return ()
    return tuple(profile.get("levels") or ())  # type: ignore[arg-type]


def default_reasoning_effort(preset: str) -> str:
    """该预设的默认档位；返回空串表示无默认（不追加厂商专属参数）。"""
    profile = REASONING_PROFILES.get(preset)
    if profile is None:
        return ""
    return str(profile.get("default") or "")


def supports_reasoning_control(preset: str) -> bool:
    return bool(reasoning_levels(preset))


def clamp_temperature(value: float) -> float:
    """把 temperature 钳制到所有在册厂商都接受的区间。

    存在的唯一理由：改写类任务想要确定性（temperature=0），但智谱明确拒绝 0。
    落到 0.1 兼顾"足够确定"与"各家合法"。
    """
    if value <= 0:
        return TEMPERATURE_MIN_DETERMINISTIC
    return min(value, TEMPERATURE_MAX)


def reasoning_params(preset: str, effort: str) -> dict[str, object]:
    """把统一档位翻译成该厂商认识的请求字段。

    未登记厂商、或档位不在支持列表时返回空 dict —— 调用方据此**不追加任何字段**，
    避免给厂商发送它不认识的参数而触发 400。
    """
    profile = REASONING_PROFILES.get(preset)
    if profile is None:
        return {}
    levels = tuple(profile.get("levels") or ())
    if effort not in levels:
        return {}
    style = profile.get("style")
    if style == "effort":
        # Kimi K3 只认 low/high/max；DeepSeek 的 none 表示关闭。
        if preset == "kimi":
            return {"reasoning_effort": effort}
        return {"reasoning_effort": "none" if effort == "off" else effort}
    if style == "thinking":
        return {"thinking": {"type": "enabled" if effort != "off" else "disabled"}}
    return {}
