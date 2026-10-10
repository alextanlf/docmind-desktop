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
    CustomProvider,
    ModelConnectionResult,
    ModelListProbe,
    ModelListView,
    ModelPresetCapabilities,
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
# 用户自建的服务商档案列表。内置预设写死在代码里，自定义的必须落库 —— 这就是
# 「自定义只能有一家、只能存一把 key」消失的地方。
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

    def model(self) -> ModelSettingsView:
        raw = self.setting_store.get(MODEL_CONFIG_KEY)
        if raw is None:
            base_url, model = MODEL_PRESETS[LEGACY_CUSTOM_PRESET]
            return ModelSettingsView(
                preset=custom_preset(DEFAULT_CUSTOM_PROVIDER_ID),
                base_url=base_url,
                model=model,
                timeout_seconds=30,
            )
        try:
            view = ModelSettingsView.model_validate_json(raw)
        except ValueError as error:
            raise DomainError("SETTINGS_INVALID", "模型设置无效，请重新配置", 500) from error
        # 升级前那家自定义厂商的 preset 是裸的 "custom"，也没有档案行。读的时候
        # 就把它当成固定 id 的「未命名」那一家的 preset —— 不写库，所以「删除供应
        # 商」之后不会被重新迁移回来。视图层再按这份配置合成它的档案行。
        if view.preset == LEGACY_CUSTOM_PRESET:
            return view.model_copy(update={"preset": custom_preset(DEFAULT_CUSTOM_PROVIDER_ID)})
        return view

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

    def custom_providers(self) -> list[CustomProvider]:
        """已落库的自定义供应商档案，按用户创建顺序。"""
        raw = self.setting_store.get(MODEL_CUSTOM_PROVIDERS_KEY)
        if not raw:
            return []
        try:
            return [CustomProvider.model_validate(item) for item in json.loads(raw)]
        except ValueError as error:
            raise DomainError(
                "SETTINGS_INVALID", "自定义模型供应商配置无效，请重新配置", 500
            ) from error

    def _serialize_custom_providers(self, providers: list[CustomProvider]) -> str:
        # `removable` 是视图状态，不是档案字段：写进存储的话，下一次读出来的档案
        # 会凭空多一个 removable，而它的真假取决于当时是谁在读。
        return json.dumps(
            [provider.model_dump(exclude={"removable"}) for provider in providers],
            ensure_ascii=False,
        )

    def _write_custom_providers(self, providers: list[CustomProvider]) -> None:
        self.setting_store.set(
            MODEL_CUSTOM_PROVIDERS_KEY, self._serialize_custom_providers(providers)
        )

    def custom_providers_view(self, saved: ModelSettingsView) -> list[CustomProvider]:
        """下拉框要显示的供应商列表，始终包含当前选中的那一家。

        当前预设是自定义、却没有对应档案行的情况有三种：升级前裸的 `custom`、
        表单刚点了「新建」还没保存、以及首次启动的空配置。这里按已保存的配置合成
        一条 `removable=False` 的条目 —— 否则下拉框没有当前值，表单会落到另一家
        身上，显示出一个用户从未选择过的厂商。
        """
        stored = self.custom_providers()
        view = [provider.model_copy(update={"removable": True}) for provider in stored]
        saved_id = custom_provider_id(saved.preset)
        if saved_id is None or any(provider.id == saved_id for provider in stored):
            return view
        return [
            *view,
            CustomProvider(
                id=saved_id,
                label=saved.label or DEFAULT_CUSTOM_LABEL,
                base_url=saved.base_url,
                model=saved.model,
                timeout_seconds=saved.timeout_seconds,
                reasoning_effort=saved.reasoning_effort,
            ),
        ]

    def _upsert_custom_provider(
        self,
        providers: list[CustomProvider],
        provider_id: str,
        label: str,
        payload: dict,
    ) -> str:
        """把表单这一份草稿写回档案，返回新的档案列表（JSON）。新的一家追加在
        末尾，下拉框顺序因此稳定；写库由调用方与配置一起做，见 `save_model`。"""
        provider = CustomProvider(
            id=provider_id,
            label=label,
            base_url=str(payload.get("baseUrl") or ""),
            model=str(payload.get("model") or ""),
            timeout_seconds=float(payload.get("timeoutSeconds") or 30),
            reasoning_effort=str(payload.get("reasoningEffort") or ""),
        )
        return self._serialize_custom_providers(
            [*[p for p in providers if p.id != provider_id], provider]
        )

    def delete_custom_provider(self, provider_id: str) -> None:
        """删掉一家自定义供应商：档案、它的 key，以及指向它的当前配置。

        没有档案行的那一家（`removable=False`）删不掉：它本来就是合成的，删完还会
        再合成一次，看起来像删除按钮失灵。界面因此不显示这个按钮，这里也不接受。
        """
        providers = self.custom_providers()
        if not any(provider.id == provider_id for provider in providers):
            raise DomainError("MODEL_PROVIDER_NOT_FOUND", "该自定义供应商不存在", 404)
        preset = custom_preset(provider_id)
        self._write_custom_providers([p for p in providers if p.id != provider_id])
        self.secret_store.delete(model_api_key_name(preset))
        if provider_id == DEFAULT_CUSTOM_PROVIDER_ID:
            # 它那把 key 可能还在升级前的旧槽位里躺着，否则删完仍会被回退读出来。
            self.secret_store.delete(model_api_key_name(LEGACY_CUSTOM_PRESET))
        if self.model().preset == preset:
            # 删掉的正是当前在用的那家：回落到「未命名」槽位。留着旧配置会让表单
            # 选中一个下拉框里不存在的预设。
            self.setting_store.set_many(
                {
                    MODEL_CONFIG_KEY: ModelSettingsView(
                        preset=LEGACY_CUSTOM_PRESET,
                        base_url="",
                        model="",
                        timeout_seconds=30,
                    ).model_dump_json(),
                    MODEL_KEY_REFERENCE: "",
                }
            )

    def model_setup_skipped(self) -> bool:
        return self.setting_store.get(MODEL_SETUP_SKIPPED_KEY) == "1"

    def skip_model_setup(self) -> None:
        self.setting_store.set(MODEL_SETUP_SKIPPED_KEY, "1")

    async def save_model(self, update: ModelSettingsUpdate) -> ModelSettingsView:
        # 预设有三类：内置厂家的固定 id、`custom:<档案 id>`、以及表单「新建」用的
        # `custom:new`（保存时才分配真正的 id）。升级前裸的 `custom` 归到那家
        # 「未命名」的固定档案上，老客户端与老表单因此仍然能存进去。
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
        providers = self.custom_providers()
        if (
            provider_id is not None
            and not creating
            and provider_id != DEFAULT_CUSTOM_PROVIDER_ID
            and not any(provider.id == provider_id for provider in providers)
        ):
            # 表单拿着一个已经被删掉的供应商在保存：宁可报错，也不能悄悄新建一家
            # 同名同 id 的，那会把「删除」变成一句谎话。
            raise DomainError("MODEL_PROVIDER_NOT_FOUND", "该自定义供应商已不存在，请重新选择", 422)
        # `WireModel.model_dump` emits alias keys, so the effort key is camelCase.
        payload = update.model_dump(exclude={"api_key"})
        payload["preset"] = preset
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
        # than "off", so switching presets never silently keeps a stale level
        # that the new vendor does not accept.
        if not payload.get("reasoningEffort"):
            payload["reasoningEffort"] = default_reasoning_effort(
                preset, str(payload.get("model") or "")
            )
        if provider_id is not None:
            # 名字留空就沿用它原来的名字，都没有才用兜底名 —— 改 Base URL 时不该
            # 顺手把用户起的名字清掉。
            label = (update.label or "").strip() or next(
                (provider.label for provider in providers if provider.id == provider_id),
                "",
            )
            payload["label"] = label or DEFAULT_CUSTOM_LABEL
        else:
            # 内置预设没有「显示名」，落一份空值，免得切到自定义时残留上一家的名字。
            payload["label"] = ""
        config = ModelSettingsView(**payload)
        serialized_config = config.model_dump_json()
        key_name = model_api_key_name(preset)
        # 任何一次保存都把旧槽位搬走，包括「只改预设、不带 key」这种：不搬的话，
        # 旧 key 会在新预设上被回退读出来，等于换个名字重演同一个 bug。
        self._migrate_legacy_api_key()
        # 档案和配置必须一起落库（同一次 set_many）：写失败的保存不该在列表里留下
        # 一家用户从没保存成功的供应商，也不该让配置指向一条不存在的档案。
        provider_values: dict[str, str] = {}
        if provider_id is not None:
            provider_values[MODEL_CUSTOM_PROVIDERS_KEY] = self._upsert_custom_provider(
                providers, provider_id, str(payload.get("label") or ""), payload
            )
        if update.api_key is None:
            # Saving any model config means the user engaged with setup, so a
            # previous skip no longer applies — and the key of *this* preset is
            # left untouched, which is what makes switching back restore it.
            self.setting_store.set_many(
                {
                    MODEL_CONFIG_KEY: serialized_config,
                    MODEL_SETUP_SKIPPED_KEY: "0",
                    **provider_values,
                }
            )
            return config

        previous_api_key = self.secret_store.get(key_name)
        if update.api_key == "":
            self.secret_store.delete(key_name)
            key_reference = ""
        else:
            self.secret_store.set(key_name, update.api_key)
            key_reference = key_name
        try:
            self.setting_store.set_many(
                {
                    MODEL_CONFIG_KEY: serialized_config,
                    MODEL_KEY_REFERENCE: key_reference,
                    MODEL_SETUP_SKIPPED_KEY: "0",
                    **provider_values,
                }
            )
        except Exception:
            if previous_api_key is None:
                self.secret_store.delete(key_name)
            else:
                self.secret_store.set(key_name, previous_api_key)
            raise
        return config

    async def test_model(self) -> ModelConnectionResult:
        api_key = self.saved_api_key()
        if not api_key:
            raise DomainError(
                "MODEL_AUTH_FAILED", "请先配置 API Key", 400, False, "保存 API Key 后重试"
            )
        return await self.provider_factory(
            ModelConfig(**self.model().model_dump()), api_key
        ).test_connection()

    async def list_models(self, probe: ModelListProbe | None = None) -> ModelListView:
        """List models for the provider the form is currently describing.

        The form's draft wins over the saved config, **including the preset**:
        the picker is driven by what the user just chose, and falling back to the
        saved preset would show a catalogue for a vendor they are not configuring.
        `GET /models` still overrides the curated list when the provider answers.
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
        curated = preset_models(preset)
        # 表单草稿里的 key 优先；否则用**被探测预设自己的槽位** —— 拿已保存预设
        # 的 key 去列另一个厂商的模型，只会得到一场 401。
        api_key = self._api_key(preset, saved.preset)
        if probe is not None and probe.api_key:
            api_key = probe.api_key
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
        providers = self.custom_providers_view(saved)
        # 每个预设各报各的「有没有 key」，前端的「已安全保存」据此判断。给单个
        # 布尔值时它只能描述「已保存的那家」，切了预设就变成谎话。
        #
        # 自定义供应商也在这张表里，按 `custom:<档案 id>` 逐家一份 —— 这正是
        # 「custom 只能记住一把 key」被修掉的地方：两家自定义厂商各有各的槽位。
        # 裸的 `custom` 不再是可选项，它已经归到 `custom:default` 那家名下。
        api_keys = {
            preset: self.has_api_key(preset, saved_preset=saved.preset)
            for preset in MODEL_PRESETS
            if preset != LEGACY_CUSTOM_PRESET
        }
        api_keys.update(
            {
                custom_preset(provider.id): self.has_api_key(
                    custom_preset(provider.id), saved_preset=saved.preset
                )
                for provider in providers
            }
        )
        return SettingsView(
            model=saved,
            has_api_key=api_keys.get(saved.preset, False),
            api_keys=api_keys,
            custom_providers=providers,
            data_path=str(settings.data_dir.resolve()),
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
    return await _service(request).test_model()


@router.delete("/model/providers/{provider_id}", response_model=SettingsView)
async def delete_model_provider(provider_id: str, request: Request) -> SettingsView:
    _service(request).delete_custom_provider(provider_id)
    return _service(request).view(_settings(request))


@router.post("/model/skip-setup", response_model=SettingsView)
async def skip_model_setup(request: Request) -> SettingsView:
    _service(request).skip_model_setup()
    return _service(request).view(_settings(request))


@router.post("/model/list", response_model=ModelListView)
async def list_models(request: Request) -> ModelListView:
    # The body is optional: a request without one lists the saved provider.
    probe: ModelListProbe | None = None
    raw = await request.body()
    if raw:
        try:
            probe = ModelListProbe.model_validate_json(raw)
        except ValueError as error:
            raise DomainError("INVALID_REQUEST", "请求参数无效", 422) from error
    models = await _service(request).list_models(probe)
    return models


@router.post("/runtime", response_model=SettingsView)
async def save_runtime(update: RuntimeSettingsInput, request: Request) -> SettingsView:
    await _service(request).save_runtime(update)
    return _service(request).view(_settings(request))


@router.post("/diagnostics/clear", status_code=204)
async def clear_diagnostics(request: Request) -> Response:
    _service(request).clear_diagnostics(_settings(request))
    return Response(status_code=204)
