from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Request, Response

from app.api.errors import DomainError
from app.config import AppSettings
from app.core.llm import AvailableModel, LLMProvider, ModelConfig, OpenAICompatibleProvider
from app.core.local_model_validation import normalize_loopback_base_url
from app.core.model_capabilities import (
    default_reasoning_effort,
    reasoning_levels,
)
from app.core.secrets import SecretStore
from app.remote.credentials import CredentialStore
from app.schemas.local_model import (
    LocalModelConfig,
    RagSettings,
    RoutingSettings,
    RuntimeSettingsInput,
)
from app.schemas.settings import (
    DEFAULT_CUSTOM_LABEL,
    DEFAULT_CUSTOM_PROVIDER_ID,
    MODEL_CATALOG,
    MODEL_PRESETS,
    NEW_CUSTOM_PROVIDER_ID,
    ConfiguredModel,
    FigureDescriptionInput,
    FigureOcrInput,
    LegacyCustomProvider,
    ModelActivation,
    ModelConnectionResult,
    ModelListProbe,
    ModelListView,
    ModelPresetCapabilities,
    ModelProfile,
    ModelSettingsUpdate,
    ModelSettingsView,
    SettingsView,
    custom_preset,
    custom_provider_id,
    is_free_model,
    model_label,
    preset_models,
)
from app.storage.repositories import SettingStore

MODEL_CONFIG_KEY = "model.config"
MODEL_KEY_REFERENCE = "model.api_key_ref"
MODEL_SETUP_SKIPPED_KEY = "model.setup_skipped"
# 用户已配置的模型列表。一个条目就是「一家供应商 + 一个模型 + 一份连接参数」，
# 所以内置厂商也能有多条（同一把 key，不同的模型/档位），自定义厂商也能有多条
# （同一家中转站上的两个模型）。`MODEL_ACTIVE_PROFILE_KEY` 指出此刻在用的是哪一条。
MODEL_PROFILES_KEY = "model.profiles"
MODEL_ACTIVE_PROFILE_KEY = "model.active_profile"
# 升级前那套「自定义供应商档案」列表。它把连接参数和模型绑成一条供应商记录，
# 内置厂商根本不在里面。现在只读它做迁移，见 `_synthesize_profiles`。
MODEL_CUSTOM_PROVIDERS_KEY = "model.custom_providers"
# 每个预设各存一把 key：厂商之间不通用，共用一个槽位会让「已安全保存」在新预设
# 下变成一句谎话 —— 用户切到 Kimi 后仍被告知那把 DeepSeek 的 key 还在生效，而它
# 根本打不通 Kimi。带预设后缀的槽位才是常态，下面这个裸名字只剩迁移用途。
MODEL_API_KEY_PREFIX = "model-api-key"
LEGACY_MODEL_API_KEY_NAME = MODEL_API_KEY_PREFIX
# 升级前那家「没有名字的自定义厂商」的 preset 字面量。它现在被读成
# `custom:default`，这个常量只剩迁移用途（老配置、老 key 槽位）。
LEGACY_CUSTOM_PRESET = "custom"
LOCAL_RUNTIME_CONFIG_KEY = "local-model.config"
# Read-only: an install that saved runtime settings before the local-model
# rename still holds its server address and model under this key.
LEGACY_OLLAMA_RUNTIME_CONFIG_KEY = "ollama.config"
MODEL_ROUTING_KEY = "model.routing"
RAG_CONFIG_KEY = "rag.config"
ProviderFactory = Callable[[ModelConfig, str], LLMProvider]

router = APIRouter(prefix="/api/settings", tags=["settings"])


def new_profile_id() -> str:
    """一条模型配置的 id。和供应商 id 同样限定小写字母数字 —— 它要进 URL。"""
    return uuid4().hex[:12]


def same_model(left: str, right: str) -> bool:
    """同一个模型 id 的宽松比较。

    用户手打的 `DeepSeek-Flash ` 和下拉框里的 `deepseek-flash` 是同一个模型；不这
    样比的话，「添加」会把同一个模型攒成第二行，而两行共用一把 key、行为也一样。
    """
    return left.strip().casefold() == right.strip().casefold()



def model_api_key_name(preset: str) -> str:
    """钥匙串里某个预设的 key 槽位名。

    key 属于厂商而不属于应用，所以槽位以预设为粒度。自定义供应商的预设是
    `custom:<档案 id>`，于是每一家各有各的槽位 —— 「自定义只能存一把 key」正是
    槽位粒度停在 `custom` 一个值上造成的。
    """
    return f"{MODEL_API_KEY_PREFIX}:{preset}"


class SettingsService:
    def __init__(
        self,
        setting_store: SettingStore,
        secret_store: SecretStore,
        provider_factory: ProviderFactory = OpenAICompatibleProvider,
        resolver=None,
        credential_store: CredentialStore | None = None,
    ) -> None:
        self.setting_store = setting_store
        self.secret_store = secret_store
        self.provider_factory = provider_factory
        self.resolver = resolver or _SettingsResolver()
        self.credential_store = credential_store

    # ── 已配置的模型列表 ────────────────────────────────────────────────────
    #
    # 存储只有两处：`MODEL_PROFILES_KEY` 是那张列表本身，`MODEL_ACTIVE_PROFILE_KEY`
    # 指出此刻在用的是哪一条。升级前的两份数据（唯一的 `model.config` + 自定义供应
    # 商档案）在**读**的时候合成一张列表，第一次写操作才落库 —— 和升级前对裸
    # `custom` 的处理一致：读路径不写库，所以「删除」之后不会被重新迁移回来。

    def _stored_profiles(self) -> list[ModelProfile] | None:
        raw = self.setting_store.get(MODEL_PROFILES_KEY)
        if raw is None:
            return None
        try:
            return [ModelProfile.model_validate(item) for item in json.loads(raw)]
        except ValueError as error:
            raise DomainError("SETTINGS_INVALID", "模型配置列表无效，请重新配置", 500) from error

    def _legacy_custom_providers(self) -> list[LegacyCustomProvider]:
        raw = self.setting_store.get(MODEL_CUSTOM_PROVIDERS_KEY)
        if not raw:
            return []
        try:
            return [LegacyCustomProvider.model_validate(item) for item in json.loads(raw)]
        except ValueError as error:
            raise DomainError(
                "SETTINGS_INVALID", "自定义模型供应商配置无效，请重新配置", 500
            ) from error

    def _saved_config(self, *, strict: bool) -> ModelSettingsView | None:
        """升级前那份唯一的配置；裸的 `custom` 归一成 `custom:default`。

        `strict` 只给「还没有列表」的路径用：那时这份配置就是全部数据，读不出来
        必须报错，而不是静默显示成「一个模型都没配」。
        """
        raw = self.setting_store.get(MODEL_CONFIG_KEY)
        if raw is None:
            return None
        try:
            view = ModelSettingsView.model_validate_json(raw)
        except ValueError as error:
            if strict:
                raise DomainError("SETTINGS_INVALID", "模型设置无效，请重新配置", 500) from error
            return None
        if view.preset == LEGACY_CUSTOM_PRESET:
            return view.model_copy(update={"preset": custom_preset(DEFAULT_CUSTOM_PROVIDER_ID)})
        return view

    def _synthesize_profiles(self) -> list[ModelProfile]:
        """把升级前的两份数据合成一张列表。

        id 必须每次读都算出同一个值：界面拿着 id 来编辑、删除、切换，随机 id 会
        让按钮指着一条下一次刷新就不存在的记录。自定义供应商沿用它的档案 id（key
        槽位 `custom:<id>` 因此原样继续有效），那份唯一配置用供应商 id 或 `legacy`。
        """
        profiles: list[ModelProfile] = []
        taken: set[str] = set()
        for provider in self._legacy_custom_providers():
            if provider.id in taken:
                continue
            taken.add(provider.id)
            profiles.append(
                ModelProfile(
                    id=provider.id,
                    preset=custom_preset(provider.id),
                    label=provider.label,
                    base_url=provider.base_url,
                    model=provider.model,
                    timeout_seconds=provider.timeout_seconds,
                    reasoning_effort=provider.reasoning_effort,
                )
            )
        saved = self._saved_config(strict=True)
        if saved is None:
            return profiles
        if any(
            profile.preset == saved.preset and same_model(profile.model, saved.model)
            for profile in profiles
        ):
            return profiles
        base = custom_provider_id(saved.preset) or "legacy"
        profile_id = base
        suffix = 0
        while profile_id in taken:
            suffix += 1
            profile_id = f"{base[:18]}{suffix}"
        profiles.append(
            ModelProfile(
                id=profile_id,
                preset=saved.preset,
                # 升级前那家「没有名字的自定义厂商」没有显示名，用兜底名 —— 列表行
                # 总得有个供应商名字可以显示。
                label=(
                    (saved.label or DEFAULT_CUSTOM_LABEL)
                    if custom_provider_id(saved.preset) is not None
                    else ""
                ),
                base_url=saved.base_url,
                model=saved.model,
                timeout_seconds=saved.timeout_seconds,
                reasoning_effort=saved.reasoning_effort,
            )
        )
        return profiles

    def _profiles_with_origin(self) -> tuple[list[ModelProfile], bool]:
        """已配置的模型，以及它是落库的还是刚从升级前数据合成出来的。"""
        stored = self._stored_profiles()
        if stored is not None:
            return stored, False
        return self._synthesize_profiles(), True

    def _active_id(self, profiles: list[ModelProfile], *, legacy: bool) -> str:
        """当前使用的那一条的 id；没有可用的就回落到第一条。"""
        stored = self.setting_store.get(MODEL_ACTIVE_PROFILE_KEY)
        if stored and any(profile.id == stored for profile in profiles):
            return stored
        # 升级前没有「当前档案」这个字段：那时生效的就是那份唯一配置。只有列表还
        # 没落库时才需要去认它，落库之后 `MODEL_ACTIVE_PROFILE_KEY` 就是唯一答案。
        if legacy:
            saved = self._saved_config(strict=False)
            if saved is not None:
                exact = next(
                    (
                        profile
                        for profile in profiles
                        if profile.preset == saved.preset and same_model(profile.model, saved.model)
                    ),
                    None,
                )
                if exact is not None:
                    return exact.id
                same_provider = next(
                    (profile for profile in profiles if profile.preset == saved.preset), None
                )
                if same_provider is not None:
                    return same_provider.id
        return profiles[0].id if profiles else ""

    def _write_profiles(self, profiles: list[ModelProfile], active_id: str) -> None:
        self.setting_store.set_many(
            {
                MODEL_PROFILES_KEY: json.dumps(
                    [profile.model_dump(exclude={"active", "has_api_key"}) for profile in profiles],
                    ensure_ascii=False,
                ),
                MODEL_ACTIVE_PROFILE_KEY: active_id,
            }
        )

    def active_profile(self) -> ModelProfile | None:
        profiles, legacy = self._profiles_with_origin()
        if not profiles:
            return None
        active_id = self._active_id(profiles, legacy=legacy)
        return next((profile for profile in profiles if profile.id == active_id), profiles[0])

    def model(self) -> ModelSettingsView:
        """此刻生效的模型配置。聊天、路由、连接测试都读它。"""
        profile = self.active_profile()
        if profile is None:
            # 一条都没配：给出和升级前「首次启动」一样的空配置，让上层照旧报
            # 「请先配置 API Key」，而不是在这里抛异常。
            return ModelSettingsView(
                preset=custom_preset(DEFAULT_CUSTOM_PROVIDER_ID),
                base_url="",
                model="",
                timeout_seconds=30,
            )
        return ModelSettingsView(
            preset=profile.preset,
            base_url=profile.base_url,
            model=profile.model,
            timeout_seconds=profile.timeout_seconds,
            reasoning_effort=profile.reasoning_effort,
            label=profile.label,
        )

    def configured_models(self) -> list[ConfiguredModel]:
        """设置页那张列表：每一条配置 + 它是不是当前在用、有没有 key。"""
        profiles, legacy = self._profiles_with_origin()
        active_id = self._active_id(profiles, legacy=legacy)
        saved_preset = self.model().preset
        return [
            ConfiguredModel(
                **profile.model_dump(),
                active=profile.id == active_id,
                has_api_key=self.has_api_key(profile.preset, saved_preset=saved_preset),
            )
            for profile in profiles
        ]

    def _api_key(self, preset: str, saved_preset: str) -> str | None:
        """某个预设的 key，未迁移的旧槽位只在它所属的预设上回退。

        旧版本先是一把全局槽位，后来按预设分槽；「没有名字的自定义厂商」在这两代
        里分别叫 `model-api-key` 和 `model-api-key:custom`，现在叫
        `model-api-key:custom:default`。旧槽位只属于写入时那家，别的预设不能借。
        """
        stored = self.secret_store.get(model_api_key_name(preset))
        if stored:
            return stored
        if preset == custom_preset(DEFAULT_CUSTOM_PROVIDER_ID):
            # 读路径不写库，所以这里按旧槽位名回退；下一次保存会真的把它搬走
            # （见 `_migrate_legacy_api_key`），否则「清除已保存的 API Key」删掉
            # 新槽位之后又会被旧槽位顶回来。
            legacy_custom = self.secret_store.get(model_api_key_name(LEGACY_CUSTOM_PRESET))
            if legacy_custom:
                return legacy_custom
        if preset == saved_preset:
            return self.secret_store.get(LEGACY_MODEL_API_KEY_NAME) or None
        return None

    def has_api_key(self, preset: str | None = None, *, saved_preset: str | None = None) -> bool:
        owner = saved_preset if saved_preset is not None else self.model().preset
        return bool(self._api_key(preset or owner, owner))

    def saved_api_key(self) -> str | None:
        """已保存配置该用的 key；路由为 local_only 时代表「不需要 key」，由调用方判断。"""
        owner = self.model().preset
        return self._api_key(owner, owner)

    def _migrate_legacy_api_key(self) -> None:
        """把升级前的旧槽位搬到 preset 的具名槽位上。

        必须在写新配置**之前**调用：此刻 `self.model()` 还是旧配置，指向当初写入
        这把 key 的那个预设。搬完删掉旧槽，否则 `_api_key` 的回退会让「清空 key」
        删不干净（删了新槽，旧槽又把它顶回来）。

        两个旧槽位各归各家：裸的 `model-api-key` 属于当时保存的那个预设；带
        `:custom` 后缀的那个属于「没有名字的自定义厂商」，它现在叫
        `custom:default`。两者都搬，一次升级就收敛到一个槽位一套命名。
        """
        try:
            owner: str | None = self.model().preset
        except DomainError:
            # 配置读不出来就不知道裸槽位那把 key 属于谁 —— 留在旧槽里，别猜。保存
            # 本身也不该因为一份坏配置而失败（它正要覆盖掉这份配置）。
            owner = None
        targets = [
            (
                model_api_key_name(LEGACY_CUSTOM_PRESET),
                custom_preset(DEFAULT_CUSTOM_PROVIDER_ID),
            )
        ]
        if owner is not None:
            targets.append((LEGACY_MODEL_API_KEY_NAME, owner))
        for legacy_name, target_preset in targets:
            legacy = self.secret_store.get(legacy_name)
            if not legacy:
                continue
            target_name = model_api_key_name(target_preset)
            # 新槽位已经有 key 时不再覆盖（那才是用户现在用的那把），但旧槽位**一定
            # 要删掉**：留着它，等用户哪天在新槽位上点「清除已保存的 API Key」，
            # `_api_key` 的回退就会用旧槽位把那把已删除的 key 复活 —— 界面说删了、
            # 实际还能用。
            if target_name != legacy_name and not self.secret_store.get(target_name):
                self.secret_store.set(target_name, legacy)
            self.secret_store.delete(legacy_name)

    def delete_model(self, profile_id: str) -> None:
        """删掉一条已配置的模型。

        只有这个供应商的最后一条配置被删掉时才顺带删 key 槽位：同一家厂商可以有两
        条配置（两个模型共用一把 key），删掉其中一条不该把另一条的凭据也带走。
        """
        profiles, legacy = self._profiles_with_origin()
        target = next((profile for profile in profiles if profile.id == profile_id), None)
        if target is None:
            raise DomainError("MODEL_PROFILE_NOT_FOUND", "该模型配置不存在", 404)
        active_id = self._active_id(profiles, legacy=legacy)
        remaining = [profile for profile in profiles if profile.id != target.id]
        if not any(profile.preset == target.preset for profile in remaining):
            self.secret_store.delete(model_api_key_name(target.preset))
            if target.preset == custom_preset(DEFAULT_CUSTOM_PROVIDER_ID):
                # 它那把 key 可能还在升级前的旧槽位里躺着，否则删完仍会被回退读出来。
                self.secret_store.delete(model_api_key_name(LEGACY_CUSTOM_PRESET))
        if active_id == target.id:
            # 删掉的正是当前在用的那条：用剩下的第一条顶上。留着指向已删除记录的
            # active id，聊天就会继续打一个已经不存在的配置。
            active_id = remaining[0].id if remaining else ""
        self._write_profiles(remaining, active_id)

    def activate_model(self, profile_id: str) -> None:
        """把某一条配置设为当前使用。"""
        profiles, _ = self._profiles_with_origin()
        if not any(profile.id == profile_id for profile in profiles):
            raise DomainError("MODEL_PROFILE_NOT_FOUND", "该模型配置不存在", 404)
        self._write_profiles(profiles, profile_id)

    def model_setup_skipped(self) -> bool:
        return self.setting_store.get(MODEL_SETUP_SKIPPED_KEY) == "1"

    def skip_model_setup(self) -> None:
        self.setting_store.set(MODEL_SETUP_SKIPPED_KEY, "1")

    async def save_model(self, update: ModelSettingsUpdate) -> ModelProfile:
        """新增或更新一条模型配置。

        预设有三类：内置厂家的固定 id、`custom:<供应商 id>`、以及表单「新建」用的
        `custom:new`（保存时才分配真正的 id）。升级前裸的 `custom` 归到那家
        「未命名」的供应商上，老客户端因此仍然能存进去。

        带 `profile_id` 就是编辑那一条；不带就是新增 —— 但先按「供应商 + 模型」认
        一条已有的，所以「添加」同一个模型两次是编辑，不是攒出两行一模一样的配置。
        """
        preset = update.preset
        if preset == LEGACY_CUSTOM_PRESET:
            preset = custom_preset(DEFAULT_CUSTOM_PROVIDER_ID)
        provider_id = custom_provider_id(preset)
        creating = provider_id == NEW_CUSTOM_PROVIDER_ID
        if creating:
            provider_id = uuid4().hex[:12]
            preset = custom_preset(provider_id)
        if provider_id is None and preset not in MODEL_PRESETS:
            raise DomainError("MODEL_PRESET_INVALID", "模型预设无效", 422)
        profiles, legacy = self._profiles_with_origin()
        if (
            provider_id is not None
            and not creating
            # `default` 是升级前那家「没有名字的自定义供应商」的固定 id：老客户端
            # （以及把 preset 写成裸 `custom` 的调用）还能照旧存进来，不必先有一条
            # 档案。别的 id 指名一家已经不存在的供应商时必须报错。
            and provider_id != DEFAULT_CUSTOM_PROVIDER_ID
            and not any(profile.preset == preset for profile in profiles)
        ):
            # 表单拿着一家已经被删掉的供应商在保存：宁可报错，也不能悄悄新建一家
            # 同 id 的，那会把「删除」变成一句谎话。
            raise DomainError("MODEL_PROVIDER_NOT_FOUND", "该自定义供应商已不存在，请重新选择", 422)
        target: ModelProfile | None = None
        if update.profile_id is not None:
            target = next(
                (profile for profile in profiles if profile.id == update.profile_id), None
            )
            if target is None:
                raise DomainError(
                    "MODEL_PROFILE_NOT_FOUND", "该模型配置已不存在，请刷新后重试", 404
                )
        else:
            target = next(
                (
                    profile
                    for profile in profiles
                    if profile.preset == preset and same_model(profile.model, update.model)
                ),
                None,
            )
        # A free-only preset bills the account for anything else, and the picker
        # cannot stop a hand-typed id, so the restriction is enforced on save.
        if not is_free_model(preset, str(update.model or "")):
            raise DomainError(
                "MODEL_NOT_FREE",
                "该预设仅支持免费模型，请从列表中选择",
                422,
                False,
                "OpenCode Zen 免费档会随官方活动轮换",
            )
        # An unset effort means "use the vendor's documented default" rather
        # than "off", so switching models never silently keeps a stale level
        # that the new vendor does not accept.
        reasoning_effort = update.reasoning_effort or default_reasoning_effort(preset, update.model)
        label = ""
        if provider_id is not None:
            # 名字留空就沿用它原来的名字，都没有才用兜底名 —— 改 Base URL 时不该
            # 顺手把用户起的名字清掉。同一家供应商的第二条配置沿用第一家起的名字。
            label = (update.label or "").strip() or (
                target.label
                if target is not None
                else next((profile.label for profile in profiles if profile.preset == preset), "")
            )
            label = label or DEFAULT_CUSTOM_LABEL
        profile = ModelProfile(
            id=target.id if target is not None else new_profile_id(),
            preset=preset,
            label=label,
            base_url=update.base_url,
            model=update.model,
            timeout_seconds=update.timeout_seconds,
            reasoning_effort=reasoning_effort,
        )
        active_id = self._active_id(profiles, legacy=legacy)
        if target is None:
            profiles = [*profiles, profile]
            # 刚配好的那一条就是用户此刻要用的：否则列表里多出一条谁也不认识的
            # 配置，聊天用的还是上一个模型。
            active_id = profile.id
        else:
            profiles = [profile if item.id == target.id else item for item in profiles]
        key_name = model_api_key_name(preset)
        # 任何一次保存都把旧槽位搬走，包括「只改预设、不带 key」这种：不搬的话，
        # 旧 key 会在新预设上被回退读出来，等于换个名字重演同一个 bug。
        self._migrate_legacy_api_key()
        values: dict[str, str] = {
            MODEL_PROFILES_KEY: json.dumps(
                [item.model_dump() for item in profiles], ensure_ascii=False
            ),
            MODEL_ACTIVE_PROFILE_KEY: active_id,
            # Saving any model config means the user engaged with setup, so a
            # previous skip no longer applies.
            MODEL_SETUP_SKIPPED_KEY: "0",
        }
        if update.api_key is None:
            # 不带 key 的保存不碰这个槽位，这正是「切回来 key 还在」的原因。
            self.setting_store.set_many(values)
            return profile
        previous_api_key = self.secret_store.get(key_name)
        if update.api_key == "":
            self.secret_store.delete(key_name)
            values[MODEL_KEY_REFERENCE] = ""
        else:
            self.secret_store.set(key_name, update.api_key)
            values[MODEL_KEY_REFERENCE] = key_name
        try:
            self.setting_store.set_many(values)
        except Exception:
            if previous_api_key is None:
                self.secret_store.delete(key_name)
            else:
                self.secret_store.set(key_name, previous_api_key)
            raise
        return profile

    async def test_model(self, probe: ModelListProbe | None = None) -> ModelConnectionResult:
        """测一次连接。带草稿的就是「测我正在编的这一条」。"""
        config, _, api_key = self._probe_target(probe)
        if not api_key:
            raise DomainError(
                "MODEL_AUTH_FAILED", "请先配置 API Key", 400, False, "保存 API Key 后重试"
            )
        return await self.provider_factory(config, api_key).test_connection()

    def _probe_target(
        self, probe: ModelListProbe | None
    ) -> tuple[ModelConfig, str, str | None]:
        """把表单草稿解析成「要连接的配置 + 供应商 + 该用的那把 key」。

        草稿优先于已保存的配置，**包括 preset**：二级页面正在编的是一个还没落库的
        模型，拿「当前使用」的那条去连只会得到一个和眼前表单无关的结论。key 同理
        —— 草稿里的 key 优先，否则用**被探测供应商自己的**槽位，拿别家的 key 去连
        只会得到一场 401。
        """
        saved = self.model()
        config = ModelConfig(**saved.model_dump())
        preset = saved.preset
        # 自定义预设也要认，否则探测会退回「已保存的那家」：用户在下拉框里选了
        # 第二家，拿到的却是第一家的内置列表和第一家的 key。
        if probe is not None and probe.preset and (
            probe.preset in MODEL_PRESETS or custom_provider_id(probe.preset)
        ):
            preset = probe.preset
            config = config.model_copy(update={"preset": probe.preset})
        if probe is not None:
            if probe.base_url:
                config = config.model_copy(update={"base_url": probe.base_url})
            if probe.model:
                config = config.model_copy(update={"model": probe.model})
        api_key = self._api_key(preset, saved.preset)
        if probe is not None and probe.api_key is not None:
            # 空串是「这把 key 不算数」（用户勾了清除），不能被回退成槽位里那把旧的；
            # 只有整份草稿都没带 key 时才沿用已保存的那把。
            api_key = probe.api_key or None
        return config, preset, api_key

    async def list_models(self, probe: ModelListProbe | None = None) -> ModelListView:
        """List models for the provider the form is currently describing.

        `GET /models` still overrides the curated list when the provider answers.
        """
        config, preset, api_key = self._probe_target(probe)
        curated = preset_models(preset)
        if not api_key:
            # Not an error: plenty of gateways list their models unauthenticated,
            # and the curated list is a valid answer. Say which one the user got.
            return ModelListView(
                models=curated,
                source="curated",
                notice=None if curated else "未填写 API Key，且该预设没有内置模型列表",
            )
        provider = self.provider_factory(config, api_key)
        lister = getattr(provider, "list_models", None)
        if lister is None:
            return ModelListView(models=curated, source="curated")
        try:
            models = await lister()
        except DomainError as error:
            # Live listing is a convenience, so a provider that rejects it must
            # not block model choice — but the reason has to reach the user,
            # otherwise a wrong key looks identical to "unsupported endpoint".
            return ModelListView(
                models=curated,
                source="curated",
                notice=f"实时获取失败（{error.message}），已显示内置列表",
            )
        # A free-only preset (OpenCode Zen) bills the balance for anything the
        # gateway reports, and its /models lists all 86 paid ids alongside the
        # free tier, so the live list has to be narrowed too — not just the
        # curated catalogue. Without a key the curated list is already free.
        models = [model for model in models if is_free_model(preset, model.id)]
        if not models:
            return ModelListView(
                models=curated,
                source="curated",
                notice="该服务商未返回可用的免费模型，已显示内置列表",
            )
        # Keep the entry for the currently selected model selectable even if the
        # provider does not report it.
        known = {model.id for model in models}
        selected = str(config.model or "")
        if selected and selected not in known and is_free_model(preset, selected):
            models = [*models, AvailableModel(id=selected, label=model_label(selected))]
        return ModelListView(models=models, source="live")

    def view(self, settings: AppSettings) -> SettingsView:
        saved = self.model()
        models = self.configured_models()
        # 每个供应商各报各的「有没有 key」，二级页面据此决定显示「已安全保存」还是
        # 「请输入 API Key」。给单个布尔值时它只能描述「当前在用的那家」，切一下供
        # 应商就变成谎话。
        #
        # 自定义供应商也在这张表里，按 `custom:<供应商 id>` 逐家一份 —— 两条配置共用
        # 一家供应商时它们指向同一个槽位，这正是 key 属于厂商而不属于某次选择的体现。
        # 裸的 `custom` 不再是可选项，它已经归到 `custom:default` 名下。
        api_keys = {
            preset: self.has_api_key(preset, saved_preset=saved.preset)
            for preset in MODEL_PRESETS
            if preset != LEGACY_CUSTOM_PRESET
        }
        api_keys.update(
            {
                model.preset: self.has_api_key(model.preset, saved_preset=saved.preset)
                for model in models
                if custom_provider_id(model.preset) is not None
            }
        )
        # 一条配置都没有时，`model()` 报的是那份空配置（`custom:default`）。它不在
        # 上面任何一张表里，但界面要按它问「这家有没有 key」—— 少了这一条，升级后
        # 仅剩旧槽位那把 key 的安装会被报成「一把 key 都没有」。
        api_keys.setdefault(
            saved.preset, self.has_api_key(saved.preset, saved_preset=saved.preset)
        )
        return SettingsView(
            model=saved,
            has_api_key=api_keys.get(saved.preset, False),
            api_keys=api_keys,
            models=models,

            data_path=str(settings.data_dir.resolve()),
            figure_ocr=self.setting_store.figure_ocr_enabled(),
            figure_description=self.setting_store.figure_description_enabled(),
            screenshot_count=_screenshot_count(settings.screenshots_dir),
            runtime=self.runtime(),
            model_presets={
                preset: preset_models(preset) for preset in MODEL_PRESETS if preset != "custom"
            },
            # Capabilities are keyed by preset then model id, because the same
            # vendor's models differ (Kimi K3 vs K2.6, GLM-5.3 vs 4.6).
            model_capabilities={
                preset: {
                    model: ModelPresetCapabilities(
                        reasoning_levels=list(reasoning_levels(preset, model)),
                        default_reasoning_effort=default_reasoning_effort(preset, model),
                    )
                    for model in MODEL_CATALOG.get(preset, ())
                }
                for preset in MODEL_PRESETS
            },
            model_setup_skipped=self.model_setup_skipped(),
        )

    def runtime(self) -> RuntimeSettingsInput:
        # The legacy key is read once so an existing Ollama setup keeps working
        # after the rename; it is only ever written under the new key.
        raw_local = self.setting_store.get(LOCAL_RUNTIME_CONFIG_KEY) or self.setting_store.get(
            LEGACY_OLLAMA_RUNTIME_CONFIG_KEY
        )
        raw_routing = self.setting_store.get(MODEL_ROUTING_KEY)
        raw_rag = self.setting_store.get(RAG_CONFIG_KEY)
        fallback = RuntimeSettingsInput()
        try:
            local = LocalModelConfig.model_validate_json(raw_local) if raw_local else fallback.local
            routing = (
                RoutingSettings.model_validate_json(raw_routing)
                if raw_routing
                else fallback.routing
            )
            rag = RagSettings.model_validate_json(raw_rag) if raw_rag else fallback.rag
        except ValueError as error:
            raise DomainError("SETTINGS_INVALID", "运行时设置无效，请重新配置", 500) from error
        return RuntimeSettingsInput(local=local, routing=routing, rag=rag)

    async def save_runtime(self, update: RuntimeSettingsInput) -> RuntimeSettingsInput:
        # Resolve/validate the complete update before touching any key so a
        # bad DNS answer cannot partially overwrite the previous runtime.
        normalized_base_url = await normalize_loopback_base_url(
            update.local.base_url, self.resolver
        )
        normalized = update.model_copy(
            update={"local": update.local.model_copy(update={"base_url": normalized_base_url})}
        )
        self.setting_store.set_many(
            {
                LOCAL_RUNTIME_CONFIG_KEY: normalized.local.model_dump_json(),
                MODEL_ROUTING_KEY: normalized.routing.model_dump_json(),
                RAG_CONFIG_KEY: normalized.rag.model_dump_json(),
            }
        )
        return normalized

    def clear_diagnostics(self, settings: AppSettings) -> None:
        directory = settings.screenshots_dir
        if directory.is_symlink():
            return
        root = directory.resolve()
        for child in root.iterdir():
            if child.is_symlink() or child.parent.resolve() != root:
                continue
            if child.is_file() and child.suffix in {".png", ".json", ".html"}:
                child.unlink()


def _screenshot_count(directory: Path) -> int:
    if directory.is_symlink() or not directory.is_dir():
        return 0
    return sum(
        1
        for child in directory.iterdir()
        if not child.is_symlink() and child.is_file() and child.suffix == ".png"
    )


class _SettingsResolver:
    async def resolve(self, host: str):
        rows = await asyncio.to_thread(socket.getaddrinfo, host, 11434, type=socket.SOCK_STREAM)
        return sorted({row[4][0] for row in rows})


def _service(request: Request) -> SettingsService:
    return request.app.state.settings_service


def _settings(request: Request) -> AppSettings:
    return request.app.state.settings


@router.get("", response_model=SettingsView)
async def get_settings(request: Request) -> SettingsView:
    return _service(request).view(_settings(request))


@router.put("/model", response_model=SettingsView)
async def save_model(update: ModelSettingsUpdate, request: Request) -> SettingsView:
    await _service(request).save_model(update)
    return _service(request).view(_settings(request))


@router.post("/model/test", response_model=ModelConnectionResult)
async def test_model(request: Request) -> ModelConnectionResult:
    # The body is optional: a request without one tests the model in use, while the
    # settings dialog sends its draft so the answer describes what is on screen.
    return await _service(request).test_model(await _optional_probe(request))


@router.delete("/model/profiles/{profile_id}", response_model=SettingsView)
async def delete_model(profile_id: str, request: Request) -> SettingsView:
    _service(request).delete_model(profile_id)
    return _service(request).view(_settings(request))


@router.post("/model/activate", response_model=SettingsView)
async def activate_model(activation: ModelActivation, request: Request) -> SettingsView:
    _service(request).activate_model(activation.profile_id)
    return _service(request).view(_settings(request))


@router.post("/model/skip-setup", response_model=SettingsView)
async def skip_model_setup(request: Request) -> SettingsView:
    _service(request).skip_model_setup()
    return _service(request).view(_settings(request))


@router.post("/model/list", response_model=ModelListView)
async def list_models(request: Request) -> ModelListView:
    # The body is optional: a request without one lists the saved provider.
    models = await _service(request).list_models(await _optional_probe(request))
    return models


async def _optional_probe(request: Request) -> ModelListProbe | None:
    """表单草稿，可为空。空请求体表示「用已保存的配置」。"""
    raw = await request.body()
    if not raw:
        return None
    try:
        return ModelListProbe.model_validate_json(raw)
    except ValueError as error:
        raise DomainError("INVALID_REQUEST", "请求参数无效", 422) from error


@router.post("/runtime", response_model=SettingsView)
async def save_runtime(update: RuntimeSettingsInput, request: Request) -> SettingsView:
    await _service(request).save_runtime(update)
    return _service(request).view(_settings(request))


@router.post("/figure-ocr", response_model=SettingsView)
async def save_figure_ocr(update: FigureOcrInput, request: Request) -> SettingsView:
    _service(request).setting_store.set_figure_ocr(update.enabled)
    return _service(request).view(_settings(request))


@router.post("/figure-description", response_model=SettingsView)
async def save_figure_description(
    update: FigureDescriptionInput, request: Request
) -> SettingsView:
    _service(request).setting_store.set_figure_description(update.enabled)
    return _service(request).view(_settings(request))


@router.post("/diagnostics/clear", status_code=204)
async def clear_diagnostics(request: Request) -> Response:
    _service(request).clear_diagnostics(_settings(request))
    return Response(status_code=204)
