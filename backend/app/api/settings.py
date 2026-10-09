from __future__ import annotations

import asyncio
import socket
from collections.abc import Callable
from pathlib import Path

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
    MODEL_CATALOG,
    MODEL_PRESETS,
    ModelConnectionResult,
    ModelListProbe,
    ModelListView,
    ModelPresetCapabilities,
    ModelSettingsUpdate,
    ModelSettingsView,
    SettingsView,
    is_free_model,
    model_label,
    preset_models,
)
from app.storage.repositories import SettingStore

MODEL_CONFIG_KEY = "model.config"
MODEL_KEY_REFERENCE = "model.api_key_ref"
MODEL_SETUP_SKIPPED_KEY = "model.setup_skipped"
# 每个预设各存一把 key：厂商之间不通用，共用一个槽位会让「已安全保存」在新预设
# 下变成一句谎话 —— 用户切到 Kimi 后仍被告知那把 DeepSeek 的 key 还在生效，而它
# 根本打不通 Kimi。带预设后缀的槽位才是常态，下面这个裸名字只剩迁移用途。
MODEL_API_KEY_PREFIX = "model-api-key"
LEGACY_MODEL_API_KEY_NAME = MODEL_API_KEY_PREFIX
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

    key 属于厂商而不属于应用，所以槽位以预设为粒度 —— 这也是 `custom` 只能记住
    一把 key 的原因，那是预设本身的粒度所限。
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
            base_url, model = MODEL_PRESETS["custom"]
            return ModelSettingsView(
                preset="custom", base_url=base_url, model=model, timeout_seconds=30
            )
        try:
            return ModelSettingsView.model_validate_json(raw)
        except ValueError as error:
            raise DomainError("SETTINGS_INVALID", "模型设置无效，请重新配置", 500) from error

    def _api_key(self, preset: str, saved_preset: str) -> str | None:
        """某个预设的 key，未迁移的旧槽位只在它所属的预设上回退。

        旧版本只有一个全局槽位，它属于**写入时保存的那个预设**。在第一次写 key
        把它搬走之前，只有那个预设读得到它 —— 别的预设不能借，那正是这个 bug 的
        根源。
        """
        stored = self.secret_store.get(model_api_key_name(preset))
        if stored:
            return stored
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
        """把升级前唯一的槽位搬到它所属预设的槽位。

        必须在写新配置**之前**调用：此刻 `self.model()` 还是旧配置，指向当初写入
        这把 key 的那个预设。搬完删掉旧槽，否则 `_api_key` 的回退会让「清空 key」
        删不干净（删了新槽，旧槽又把它顶回来）。
        """
        legacy = self.secret_store.get(LEGACY_MODEL_API_KEY_NAME)
        if not legacy:
            return
        try:
            owner = self.model().preset
        except DomainError:
            # 配置读不出来就不知道这把 key 属于谁 —— 留在旧槽里，别猜。保存本身
            # 也不该因为一份坏配置而失败（它正要覆盖掉这份配置）。
            return
        if not self.secret_store.get(model_api_key_name(owner)):
            self.secret_store.set(model_api_key_name(owner), legacy)
        self.secret_store.delete(LEGACY_MODEL_API_KEY_NAME)

    def model_setup_skipped(self) -> bool:
        return self.setting_store.get(MODEL_SETUP_SKIPPED_KEY) == "1"

    def skip_model_setup(self) -> None:
        self.setting_store.set(MODEL_SETUP_SKIPPED_KEY, "1")

    async def save_model(self, update: ModelSettingsUpdate) -> ModelSettingsView:
        if update.preset not in MODEL_PRESETS:
            raise DomainError("MODEL_PRESET_INVALID", "模型预设无效", 422)
        # A free-only preset bills the account for anything else, and the picker
        # cannot stop a hand-typed id, so the restriction is enforced on save.
        if not is_free_model(update.preset, str(update.model or "")):
            raise DomainError(
                "MODEL_NOT_FREE",
                "该预设仅支持免费模型，请从列表中选择",
                422,
                False,
                "OpenCode Zen 免费档会随官方活动轮换",
            )
        # `WireModel.model_dump` emits alias keys, so the effort key is camelCase.
        payload = update.model_dump(exclude={"api_key"})
        # An unset effort means "use the vendor's documented default" rather
        # than "off", so switching presets never silently keeps a stale level
        # that the new vendor does not accept.
        if not payload.get("reasoningEffort"):
            payload["reasoningEffort"] = default_reasoning_effort(
                update.preset, str(payload.get("model") or "")
            )
        config = ModelSettingsView(**payload)
        serialized_config = config.model_dump_json()
        key_name = model_api_key_name(update.preset)
        # 任何一次保存都把旧槽位搬走，包括「只改预设、不带 key」这种：不搬的话，
        # 旧 key 会在新预设上被回退读出来，等于换个名字重演同一个 bug。
        self._migrate_legacy_api_key()
        if update.api_key is None:
            # Saving any model config means the user engaged with setup, so a
            # previous skip no longer applies — and the key of *this* preset is
            # left untouched, which is what makes switching back restore it.
            self.setting_store.set_many(
                {MODEL_CONFIG_KEY: serialized_config, MODEL_SETUP_SKIPPED_KEY: "0"}
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
        if probe is not None and probe.preset in MODEL_PRESETS:
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
        # 每个预设各报各的「有没有 key」，前端的「已安全保存」据此判断。给单个
        # 布尔值时它只能描述「已保存的那家」，切了预设就变成谎话。
        api_keys = {
            preset: self.has_api_key(preset, saved_preset=saved.preset)
            for preset in MODEL_PRESETS
        }
        return SettingsView(
            model=saved,
            has_api_key=api_keys.get(saved.preset, False),
            api_keys=api_keys,
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
