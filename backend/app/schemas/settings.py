from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Literal

from pydantic import Field

from app.core.llm import AvailableModel, ModelConfig, ModelConnectionResult, model_label
from app.schemas.common import WireModel
from app.schemas.local_model import RuntimeSettingsInput

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
    # 升级前唯一的那家自定义厂商，没有名字、也没有档案行。它仍然留在表里有两个
    # 理由：老配置里存的 preset 就是它（读的时候要认得出来），以及前端那张内置
    # 预设表与这张表必须逐字对齐。**它不再出现在预设下拉框里** —— 现在每一家
    # 自定义厂商都有自己的档案和槽位，见下面的 CUSTOM_PRESET_PREFIX。
    "custom": ("", ""),
}

# 自定义供应商的预设名是 `custom:<档案 id>`。
#
# 🔴 为什么需要它：预设原来是固定枚举，`custom` 只是其中一个值，于是「自定义」
# 天然只有一家 —— 一把 `model-api-key:custom` 的 key、一份 model.config，换第二家
# 中转站就只能覆盖第一家的钥匙。用户自建的厂商是**列表**，不是枚举，所以它的
# 标识必须能把任意多家区分开。
CUSTOM_PRESET_PREFIX = "custom:"
# 没有档案行的那家「未命名自定义」的固定 id。固定成常量而不是随机 id，读取时
# 才能把它虚拟成一条可选项，而不用在读路径上写库。
DEFAULT_CUSTOM_PROVIDER_ID = "default"
# 表单「新建」用的占位 id：保存时换成随机 id，绝不落库。
NEW_CUSTOM_PROVIDER_ID = "new"
# 没填名字时的兜底显示名。
DEFAULT_CUSTOM_LABEL = "自定义"
_PROVIDER_ID_PATTERN = re.compile(r"[a-z0-9]{1,20}")


def custom_preset(provider_id: str) -> str:
    """一家自定义供应商的预设 id。"""
    return f"{CUSTOM_PRESET_PREFIX}{provider_id}"


def custom_provider_id(preset: str) -> str | None:
    """预设 id 里的供应商 id；不是自定义预设（或后缀不合法）就是 None。"""
    if not preset.startswith(CUSTOM_PRESET_PREFIX):
        return None
    provider_id = preset[len(CUSTOM_PRESET_PREFIX) :]
    return provider_id if _PROVIDER_ID_PATTERN.fullmatch(provider_id) else None


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
    # (200 without a key, 87 models, 13 free once Big Pickle is counted) plus
    # the pricing table on opencode.ai/docs/zen. Paid ids are excluded on
    # purpose — see FREE_ONLY_PRESETS. This tier rotates without notice, so the
    # live list is what the picker trusts once a key exists; this curated copy
    # is what it falls back to. `test_zen_catalogue_matches_live_snapshot` keeps
    # the two in sync.
    #
    # 顺序规则：默认模型提到首位（`test_preset_defaults_match_catalogue_heads`
    # 要求它的位置），其余**按线上响应的顺序**。这样任何一次重抓都能复现同一份
    # 清单，不必凭人记"上次是什么顺序"。
    #
    # 2026-10-10 重抓前的教训：旧清单里的 deepseek-v4-flash-free、fledge-alpha-free、
    # mimo-v2.5-free 已经下线，而 exo-free、step-5-preview-free 是新上线的。快照
    # 比对测试当时是**绿的** —— 因为它比的是旧快照而不是线上，两侧一起过期。
    # `test_no_catalogue_is_past_its_review_deadline` 就是为这个盲区加的。
    "opencode_zen": (
        "mimo-v2.6-flash-free",
        "big-pickle",
        "jev-1.13-free",
        "exo-free",
        "muse-spark-1.3-contributor-free",
        "muse-spark-1.2-contributor-free",
        "space-bunny-free",
        "longcat-2.5-preview-free",
        "step-5-preview-free",
        "ling-3.0-flash-fin-free",
        "nemotron-3-ultra-free",
        "nemotron-3.5-lightning-free",
        "ling-3.1-flash-free",
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


# 核对某份目录时该看哪些**公开**页面，按预设分组。
#
# 存在的理由：闸门（见下面的 stale_catalogs）只会说"这家该核对了"，不说"去哪核
# 对"。没有这份清单，响应闸门的人要么去猜页面，要么去翻代码注释里散落的域名。
# 把来源写进数据，闸门才算可执行。
#
# 🔴 全部是公开页面，**都不需要 API key**。这一点是硬约束，不是巧合：DocMind 装
# 在用户机子上，key 只在用户自己的钥匙串里，任何"拿 key 去定期核对"的方案都要求
# 凭据离开用户机器。要核对模型 id 是否改名/下线，厂商公开文档就够了。
#
# 2026-10-10 逐一实测，全部返回 200。
MODEL_CATALOG_DOC_SOURCES: dict[str, tuple[str, ...]] = {
    "deepseek": ("https://api-docs.deepseek.com/guides/thinking_mode",),
    "qwen": ("https://help.aliyun.com/zh/document_detail/2867560.html",),
    "kimi": (
        "https://platform.moonshot.cn/docs/guide/use-thinking-models",
        "https://platform.moonshot.cn/docs/guide/use-thinking-effort",
    ),
    "glm": (
        "https://docs.bigmodel.cn/cn/guide/start/concept-param/",
        "https://docs.z.ai/guides/llm/glm-5.3",
    ),
    "mimo": ("https://platform.xiaomimimo.com/docs/usage-guide/passing-back-reasoning_content",),
    "opencode_zen": ("https://opencode.ai/docs/zen",),
    "opencode_go": ("https://opencode.ai/docs/zen",),
    "openai": (
        "https://platform.openai.com/docs/models",
        "https://platform.openai.com/docs/guides/latest-model",
        "https://learn.microsoft.com/azure/ai-services/openai/how-to/reasoning",
    ),
}

# 免 key 就能抓的 `/models` 端点。**只有这些**能进开发者侧的自动巡检 —— 其余厂商
# 的 `/models` 要凭据，那条路已被上面那段约束堵死。所以自动化和人读文档是互补的：
# 网关（模型池随时增删）走抓取，直连厂商（id 由厂商命名周期决定）走文档。
# 2026-10-10 实测：两者均 200，Zen 87 条、Go 45 条。
MODEL_CATALOG_KEYLESS_MODELS_ENDPOINTS: dict[str, str] = {
    "opencode_zen": "https://opencode.ai/zen/v1/models",
    "opencode_go": "https://opencode.ai/zen/go/v1/models",
}

# 每份目录的"最后核对日期"（YYYY-MM-DD）。
#
# 🔴 它记录的**不是**"这份清单此刻一定正确"，而是"某人最后一次带着依据看过它的
# 时间"。厂商不会通知我们它昨天上了什么新模型，所以"有没有人去看过"是唯一能离线
# 检查的东西，闸门检查的就是它。
#
# 更新规则：**改动某个预设的目录行时，同一次提交里把它的日期贴到当天。**
# 忘了贴是安全的失效方向 —— 日期偏旧只会让闸门提前响，不会让过期目录蒙混过关。
#
# 取值来源必须说清楚：除 opencode_zen 外，其余都是 git blame 上该预设目录行的
# 最后改动日期。**这不等同于"发布时逐家回访过厂商文档"** —— 引入闸门时并没有
# 那样做过，把它们写成"今天已核对"会是假的。闸门的价值恰恰在于逼出这次回访：
# 到期时它会指名道姓地说哪家该看了。
MODEL_CATALOG_REVIEWED_AT: dict[str, str] = {
    "deepseek": "2026-10-04",
    "qwen": "2026-10-06",
    "kimi": "2026-10-04",
    "glm": "2026-10-04",
    "mimo": "2026-10-04",
    # 唯一一个真的在当天核对过的：2026-10-10 抓了线上 /models，据此改写了目录，
    # 并重抓了 tests/fixtures/opencode_zen_models.json。
    "opencode_zen": "2026-10-10",
    "opencode_go": "2026-10-04",
    "openai": "2026-10-06",
}

# 核对周期上限（天）。超过就认为这份目录可能在骗人：用户会看到一个已经改名或下线
# 的 id，而且是**在没有 key 的首次设置里**看到 —— 那时 live `/models` 还没法兜底。
MODEL_CATALOG_MAX_AGE_DAYS = 180

# 会随时轮换的模型池，周期收紧。Zen 的免费档官方明说 "rotates without notice"
# （见 MODEL_CATALOG 里那段注释），半年一次的核对对它太松了：2026-10-10 那次实测
# 就发现 3 个已下线、2 个新上线，而当时距上次登记只有 6 天。
MODEL_CATALOG_MAX_AGE_DAYS_BY_PRESET: dict[str, int] = {
    "opencode_zen": 90,
}


def catalog_max_age_days(preset: str) -> int:
    """该预设的核对周期上限。"""
    return MODEL_CATALOG_MAX_AGE_DAYS_BY_PRESET.get(preset, MODEL_CATALOG_MAX_AGE_DAYS)


def catalog_review_today() -> date:
    """核对口径的「今天」。

    🔴 固定用 UTC，不用本地时区。闸门必须在所有人的机器上**同一天**响 —— 用本地
    日历的话，UTC+13 与 UTC-11 的开发者算出的目录年龄会差一天，"这家该核对了"
    就成了因人而异的事，而排查这种差异要花的时间远超过它省下的。
    """
    return datetime.now(tz=UTC).date()


def stale_catalogs(today: date | None = None) -> list[tuple[str, str, int, int]]:
    """已超过核对周期的预设，返回 ``(preset, 核对日期, 已过天数, 周期上限)``。

    `today` 可注入，好让"闸门本身会不会响"被测试钉住 —— 否则它要么永远返回空
    列表（装饰），要么在某天毫无预兆地对所有人变红，两种都没法验证。

    没有目录的预设（`custom`）不参与：没有厂商可核对。
    """
    current = today or catalog_review_today()
    stale: list[tuple[str, str, int, int]] = []
    for preset in MODEL_CATALOG:
        reviewed = MODEL_CATALOG_REVIEWED_AT.get(preset)
        if reviewed is None:
            continue
        limit = catalog_max_age_days(preset)
        age = (current - date.fromisoformat(reviewed)).days
        if age > limit:
            stale.append((preset, reviewed, age, limit))
    return stale


# 厂商已下线、或已被新一代 id 取代的模型，按**直连厂商**分组。
#
# 🔴 存在的理由：目录里混进一个已经调不通的模型，用户是在下拉框里选中它、请求
# 400 之后才知道 —— 而这件事本来只需要"有人读过下线公告"。把它写成常量，
# `test_catalogue_never_lists_a_retired_model` 就能把那条公告变成一条被强制的不
# 变量：公告读到了就登记一行，从此目录再也收不进它。**只读公告而不落在这里，
# 等于没读。**
#
# 保留这些 id 在能力表/显示名表里的登记是**故意的**：老配置存的正是这些字符串，
# 删掉它们不会给出任何提示，只会让老用户的推理档位静默消失。
#
# 按厂商分组而不是拉平成一个集合，是因为聚合网关不受直连厂商的下线约束：
# OpenCode Go 至今仍在提供 `mimo-v2.5`（见 MODEL_CATALOG），小米官方的下线公告
# 管不到它。用 `RETIRED_MODELS[preset]` 查就自然表达了这个区分，不需要在下游
# 写 `if preset in gateways: continue` 这类例外。
#
# 来源：DeepSeek 2026-07-24 下线 deepseek-chat / deepseek-reasoner（现名为
# deepseek-flash 与 deepseek-v4-pro）；小米 MiMo 公告 V2.5 系列 2026-10-21 下线；
# OpenAI 将 gpt-5-mini / gpt-5.1 标为 Deprecated；通义改用 Qwen3.5+ 世代 id
# 取代 qwen-plus 这类按规格命名的档位。
RETIRED_MODELS: dict[str, frozenset[str]] = {
    "deepseek": frozenset({"deepseek-chat", "deepseek-reasoner"}),
    "qwen": frozenset({"qwen-plus"}),
    "mimo": frozenset({"mimo-v2.5", "mimo-v2.5-pro"}),
    "openai": frozenset({"gpt-5-mini", "gpt-5.1"}),
}


def is_retired_model(preset: str, model_id: str) -> bool:
    """该「厂商 + 模型」是否已下线。聚合网关自身没有下线记录，一律 False。"""
    return model_id in RETIRED_MODELS.get(preset, frozenset())


def preset_models(preset: str) -> list[AvailableModel]:
    """Curated models for a preset, labeled for display."""
    return [
        AvailableModel(id=model_id, label=model_label(model_id))
        for model_id in MODEL_CATALOG.get(preset, ())
        if is_free_model(preset, model_id)
    ]


class CustomProvider(WireModel):
    """一家用户自建的 OpenAI 兼容供应商。

    它是「一份连接配置 + 一个 API Key 槽位」的载体。内置预设由代码定义，一家一个；
    自定义供应商由用户增删，数量不定，所以必须落库成列表 —— 只用一个 `custom`
    预设字符串既表达不了「第二家」，也就只能记住一把 key。

    `id` 只出现在预设 id 的后缀与钥匙串槽位名里，所以限定小写字母数字：它要能安全
    地进 URL、进钥匙串条目名，也不能长得让 `custom:<id>` 超过预设字段的长度上限。
    """

    id: str = Field(pattern=r"^[a-z0-9]{1,20}$")
    label: str = Field(min_length=1, max_length=60)
    base_url: str = ""
    model: str = ""
    timeout_seconds: float = Field(default=30, gt=0, le=300)
    reasoning_effort: str = ""
    # 只给界面看：这一项背后是否真有一条档案可删。升级前裸的 `custom`、还没保存过
    # 的新建、以及首次启动的空白配置都会在读取时被合成为一条，删掉它只会再合成
    # 一次，所以「删除」按钮不该出现。它属于视图而不是档案，落库时被
    # `_serialize_custom_providers` 摘掉。
    removable: bool = False


class ModelSettingsUpdate(ModelConfig):
    api_key: str | None = None
    # 自定义供应商的显示名。内置预设留空：它们的名字由前端那张内置表提供，落库
    # 只会多一份会过期的副本。
    label: str = Field(default="", max_length=60)


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
    # 当前这家供应商的显示名，只对自定义供应商有值 —— 内置预设的名字在渲染层
    # 的内置表里，视图不重复一份会过期的副本。
    label: str = Field(default="", max_length=60)


class ModelPresetCapabilities(WireModel):
    """What a preset supports, so the UI can offer only valid choices."""

    reasoning_levels: list[str] = Field(default_factory=list)
    default_reasoning_effort: str = ""


class SettingsView(WireModel):
    model: ModelSettingsView
    has_api_key: bool
    # Per-preset "a key is stored for this vendor". API keys are not
    # interchangeable between vendors, so a single boolean could only ever
    # describe the saved preset — the form read it as "the key you see applies
    # to whatever preset is selected", which was false the moment the user
    # switched. The bare `has_api_key` stays for the saved preset so the
    # first-run gate keeps its meaning.
    api_keys: dict[str, bool] = Field(default_factory=dict)
    # 用户自建的服务商，可增删。列表里始终包含当前选中的那一家（没有档案行时由
    # 服务层按已保存的配置合成），否则下拉框会缺少当前值、表单会显示成另一家。
    custom_providers: list[CustomProvider] = Field(default_factory=list)
    data_path: str
    screenshot_count: int
    runtime: RuntimeSettingsInput | None = None
    # Curated per-preset catalogue so the model picker has choices on first
    # paint, before an API key is entered and before any live query.
    model_presets: dict[str, list[AvailableModel]] = Field(default_factory=dict)
    # Reasoning support keyed by preset, then by model id — the same vendor's
    # models differ (Kimi K3 vs K2.6, GLM-5.3 vs 4.6). The nesting must match
    # what SettingsService.view() builds: declaring the inner value as a bare
    # ModelPresetCapabilities makes pydantic silently drop every model's entry
    # and emit default-empty capabilities under each preset.
    model_capabilities: dict[str, dict[str, ModelPresetCapabilities]] = Field(default_factory=dict)
    # True once the user dismissed first-run setup, so it is not shown again.
    model_setup_skipped: bool = False


__all__ = [
    "CUSTOM_PRESET_PREFIX",
    "DEFAULT_CUSTOM_LABEL",
    "DEFAULT_CUSTOM_PROVIDER_ID",
    "FREE_ONLY_PRESETS",
    "MODEL_CATALOG",
    "MODEL_CATALOG_DOC_SOURCES",
    "MODEL_CATALOG_KEYLESS_MODELS_ENDPOINTS",
    "MODEL_CATALOG_MAX_AGE_DAYS",
    "MODEL_CATALOG_MAX_AGE_DAYS_BY_PRESET",
    "MODEL_CATALOG_REVIEWED_AT",
    "MODEL_PRESETS",
    "NEW_CUSTOM_PROVIDER_ID",
    "RETIRED_MODELS",
    "AvailableModel",
    "CustomProvider",
    "ModelConfig",
    "ModelConnectionResult",
    "ModelListProbe",
    "ModelListView",
    "ModelPresetCapabilities",
    "ModelSettingsUpdate",
    "ModelSettingsView",
    "ModelSetupSkip",
    "SettingsView",
    "catalog_max_age_days",
    "catalog_review_today",
    "custom_preset",
    "custom_provider_id",
    "is_free_model",
    "is_retired_model",
    "model_label",
    "preset_models",
    "stale_catalogs",
]
