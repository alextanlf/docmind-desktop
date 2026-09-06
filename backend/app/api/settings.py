from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, Request, Response

from app.api.errors import DomainError
from app.config import AppSettings
from app.core.llm import LLMProvider, ModelConfig, OpenAICompatibleProvider
from app.core.secrets import SecretStore
from app.schemas.settings import (
    MODEL_PRESETS,
    ModelConnectionResult,
    ModelSettingsUpdate,
    ModelSettingsView,
    SettingsView,
    WebSearchSettingsUpdate,
)
from app.schemas.web_search import SearchConnectionResult, WebSearchSettings
from app.search.tavily import TavilyProvider
from app.storage.repositories import SettingStore

MODEL_CONFIG_KEY = "model.config"
MODEL_KEY_REFERENCE = "model.api_key_ref"
MODEL_API_KEY_NAME = "model-api-key"
WEB_SEARCH_CONFIG_KEY = "web-search.config"
WEB_SEARCH_API_KEY_NAME = "web-search:tavily"
ProviderFactory = Callable[[ModelConfig, str], LLMProvider]

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsService:
    def __init__(
        self,
        setting_store: SettingStore,
        secret_store: SecretStore,
        provider_factory: ProviderFactory = OpenAICompatibleProvider,
    ) -> None:
        self.setting_store = setting_store
        self.secret_store = secret_store
        self.provider_factory = provider_factory

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

    async def save_model(self, update: ModelSettingsUpdate) -> ModelSettingsView:
        if update.preset not in MODEL_PRESETS:
            raise DomainError("MODEL_PRESET_INVALID", "模型预设无效", 422)
        config = ModelSettingsView(**update.model_dump(exclude={"api_key"}))
        serialized_config = config.model_dump_json()
        if update.api_key is None:
            self.setting_store.set_many({MODEL_CONFIG_KEY: serialized_config})
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

    def view(self, settings: AppSettings) -> SettingsView:
        return SettingsView(
            model=self.model(),
            has_api_key=self.has_api_key(),
            data_path=str(settings.data_dir.resolve()),
            screenshot_count=_screenshot_count(settings.screenshots_dir),
            web_search=self.web_search(),
        )

    def web_search(self) -> WebSearchSettings:
        raw = self.setting_store.get(WEB_SEARCH_CONFIG_KEY)
        if raw:
            config = WebSearchSettings.model_validate_json(raw)
        else:
            config = WebSearchSettings()
        return config.model_copy(update={"has_api_key": bool(self.secret_store.get(WEB_SEARCH_API_KEY_NAME))})

    def save_web_search(self, update: WebSearchSettingsUpdate) -> WebSearchSettings:
        if update.mode not in {"off", "ask", "auto"} or not 1 <= update.max_results <= 10:
            raise DomainError("SEARCH_SETTINGS_INVALID", "联网搜索设置无效", 422)
        previous = self.secret_store.get(WEB_SEARCH_API_KEY_NAME)
        if update.api_key is not None:
            if update.api_key:
                self.secret_store.set(WEB_SEARCH_API_KEY_NAME, update.api_key)
            else:
                self.secret_store.delete(WEB_SEARCH_API_KEY_NAME)
        try:
            self.setting_store.set_many({WEB_SEARCH_CONFIG_KEY: WebSearchSettings(mode=update.mode, max_results=update.max_results, has_api_key=False).model_dump_json()})
        except Exception:
            if previous is None: self.secret_store.delete(WEB_SEARCH_API_KEY_NAME)
            else: self.secret_store.set(WEB_SEARCH_API_KEY_NAME, previous)
            raise
        return self.web_search()

    async def test_web_search(self) -> SearchConnectionResult:
        key = self.secret_store.get(WEB_SEARCH_API_KEY_NAME)
        if not key:
            raise DomainError("SEARCH_AUTH_FAILED", "请先配置搜索 API Key", 400)
        return await TavilyProvider(key).test_connection()

    def clear_diagnostics(self, settings: AppSettings) -> None:
        directory = settings.screenshots_dir
        if directory.is_symlink():
            return
        root = directory.resolve()
        for child in root.iterdir():
            if child.is_symlink() or child.parent.resolve() != root:
                continue
            if child.is_file() and child.suffix == ".png":
                child.unlink()


def _screenshot_count(directory: Path) -> int:
    if directory.is_symlink() or not directory.is_dir():
        return 0
    return sum(
        1
        for child in directory.iterdir()
        if not child.is_symlink() and child.is_file() and child.suffix == ".png"
    )


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
