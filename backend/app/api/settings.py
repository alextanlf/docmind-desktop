from __future__ import annotations

import asyncio
import socket
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, Request, Response

from app.api.errors import DomainError
from app.config import AppSettings
from app.core.llm import AvailableModel, LLMProvider, ModelConfig, OpenAICompatibleProvider
from app.core.model_capabilities import (
    default_reasoning_effort,
    reasoning_levels,
)
from app.core.ollama_validation import normalize_loopback_base_url
from app.core.secrets import SecretStore
from app.remote.credentials import CredentialStore
from app.schemas.ollama import OllamaConfig, RagSettings, RoutingSettings, RuntimeSettingsInput
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
    WebSearchSettingsUpdate,
    is_free_model,
    model_label,
    preset_models,
)
from app.schemas.web_search import SearchConnectionResult, WebSearchSettings
from app.search.model_native import ModelSearchProvider, detect_native_search
from app.search.searxng import SearxngProvider
from app.search.tavily import TavilyProvider
from app.storage.repositories import SettingStore

MODEL_CONFIG_KEY = "model.config"
MODEL_KEY_REFERENCE = "model.api_key_ref"
MODEL_SETUP_SKIPPED_KEY = "model.setup_skipped"
MODEL_API_KEY_NAME = "model-api-key"
WEB_SEARCH_CONFIG_KEY = "web-search.config"
WEB_SEARCH_API_KEY_NAME = "web-search:tavily"
OLLAMA_RUNTIME_CONFIG_KEY = "ollama.config"
MODEL_ROUTING_KEY = "model.routing"
RAG_CONFIG_KEY = "rag.config"
ProviderFactory = Callable[[ModelConfig, str], LLMProvider]

router = APIRouter(prefix="/api/settings", tags=["settings"])


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

    def has_api_key(self) -> bool:
        return bool(self.secret_store.get(MODEL_API_KEY_NAME))

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
        if update.api_key is None:
            # Saving any model config means the user engaged with setup, so a
            # previous skip no longer applies.
            self.setting_store.set_many(
                {MODEL_CONFIG_KEY: serialized_config, MODEL_SETUP_SKIPPED_KEY: "0"}
            )
            return config

        previous_api_key = self.secret_store.get(MODEL_API_KEY_NAME)
        if update.api_key == "":
            self.secret_store.delete(MODEL_API_KEY_NAME)
            key_reference = ""
        else:
            self.secret_store.set(MODEL_API_KEY_NAME, update.api_key)
            key_reference = MODEL_API_KEY_NAME
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
                self.secret_store.delete(MODEL_API_KEY_NAME)
            else:
                self.secret_store.set(MODEL_API_KEY_NAME, previous_api_key)
            raise
        return config

    async def test_model(self) -> ModelConnectionResult:
        api_key = self.secret_store.get(MODEL_API_KEY_NAME)
        if not api_key:
            raise DomainError("MODEL_AUTH_FAILED", "请先配置 API Key", 400, False, "保存 API Key 后重试")
        return await self.provider_factory(ModelConfig(**self.model().model_dump()), api_key).test_connection()

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
        api_key = self.secret_store.get(MODEL_API_KEY_NAME)
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
        return SettingsView(
            model=self.model(),
            has_api_key=self.has_api_key(),
            data_path=str(settings.data_dir.resolve()),
            screenshot_count=_screenshot_count(settings.screenshots_dir),
            web_search=self.web_search(),
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
        raw_ollama = self.setting_store.get(OLLAMA_RUNTIME_CONFIG_KEY)
        raw_routing = self.setting_store.get(MODEL_ROUTING_KEY)
        raw_rag = self.setting_store.get(RAG_CONFIG_KEY)
        fallback = RuntimeSettingsInput()
        try:
            ollama = OllamaConfig.model_validate_json(raw_ollama) if raw_ollama else fallback.ollama
            routing = RoutingSettings.model_validate_json(raw_routing) if raw_routing else fallback.routing
            rag = RagSettings.model_validate_json(raw_rag) if raw_rag else fallback.rag
        except ValueError as error:
            raise DomainError("SETTINGS_INVALID", "运行时设置无效，请重新配置", 500) from error
        return RuntimeSettingsInput(ollama=ollama, routing=routing, rag=rag)

    async def save_runtime(self, update: RuntimeSettingsInput) -> RuntimeSettingsInput:
        # Resolve/validate the complete update before touching either key so a
        # bad DNS answer cannot partially overwrite the previous runtime.
        normalized_base_url = await normalize_loopback_base_url(
            update.ollama.base_url, self.resolver
        )
        normalized = update.model_copy(
            update={
                "ollama": update.ollama.model_copy(update={"base_url": normalized_base_url})
            }
        )
        self.setting_store.set_many(
            {
                OLLAMA_RUNTIME_CONFIG_KEY: normalized.ollama.model_dump_json(),
                MODEL_ROUTING_KEY: normalized.routing.model_dump_json(),
                RAG_CONFIG_KEY: normalized.rag.model_dump_json(),
            }
        )
        return normalized

    def web_search(self) -> WebSearchSettings:
        raw = self.setting_store.get(WEB_SEARCH_CONFIG_KEY)
        if raw:
            config = WebSearchSettings.model_validate_json(raw)
        else:
            config = WebSearchSettings()
        model_search_available = False
        model_search_label = ""
        try:
            support = detect_native_search(self.model())
            if support is None:
                model_search_label = "当前模型不支持内置联网，自动跳过"
            elif self.runtime().routing.mode == "local_only":
                model_search_label = "本地模式不调用云端联网，自动跳过"
            elif not self.secret_store.get(MODEL_API_KEY_NAME):
                model_search_label = "需要配置模型 API Key"
            else:
                model_search_available = True
                model_search_label = f"可用 · {support.label}"
        except (DomainError, OSError):
            pass
        try:
            has_api_key = bool(self.secret_store.get(WEB_SEARCH_API_KEY_NAME))
        except (DomainError, OSError):
            has_api_key = False
        return config.model_copy(
            update={
                "has_api_key": has_api_key,
                "model_search_available": model_search_available,
                "model_search_label": model_search_label,
            }
        )

    def save_web_search(self, update: WebSearchSettingsUpdate) -> WebSearchSettings:
        if update.mode not in {"off", "ask", "auto"} or not 1 <= update.max_results <= 10:
            raise DomainError("SEARCH_SETTINGS_INVALID", "联网搜索设置无效", 422)
        try:
            config = WebSearchSettings(
                mode=update.mode,
                max_results=update.max_results,
                query_rewrite=update.query_rewrite,
                searxng_url=update.searxng_url,
                has_api_key=False,
            )
        except ValueError as error:
            raise DomainError(
                "SEARCH_SETTINGS_INVALID", "SearXNG 实例地址无效", 422
            ) from error
        previous = self.secret_store.get(WEB_SEARCH_API_KEY_NAME)
        if update.api_key is not None:
            if update.api_key:
                self.secret_store.set(WEB_SEARCH_API_KEY_NAME, update.api_key)
            else:
                self.secret_store.delete(WEB_SEARCH_API_KEY_NAME)
        try:
            self.setting_store.set_many({WEB_SEARCH_CONFIG_KEY: config.model_dump_json()})
        except Exception:
            if previous is None: self.secret_store.delete(WEB_SEARCH_API_KEY_NAME)
            else: self.secret_store.set(WEB_SEARCH_API_KEY_NAME, previous)
            raise
        return self.web_search()

    async def test_web_search(self) -> SearchConnectionResult:
        model_provider = ModelSearchProvider(
            lambda: (self.model(), self.secret_store.get(MODEL_API_KEY_NAME))
        )
        if model_provider.available():
            return await model_provider.test_connection()
        key = self.secret_store.get(WEB_SEARCH_API_KEY_NAME)
        if key:
            return await TavilyProvider(key).test_connection()
        instance = self.web_search().searxng_url
        if instance:
            return await SearxngProvider(instance).test_connection()
        return SearchConnectionResult(
            ok=True, provider="bing", message="将使用免费兜底（Bing / DuckDuckGo）"
        )

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
        rows = await asyncio.to_thread(
            socket.getaddrinfo, host, 11434, type=socket.SOCK_STREAM
        )
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


@router.put("/web-search", response_model=SettingsView)
async def save_web_search(update: WebSearchSettingsUpdate, request: Request) -> SettingsView:
    _service(request).save_web_search(update)
    return _service(request).view(_settings(request))


@router.post("/web-search/test", response_model=SearchConnectionResult)
async def test_web_search(request: Request) -> SearchConnectionResult:
    return await _service(request).test_web_search()
