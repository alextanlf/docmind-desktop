from __future__ import annotations

import asyncio
import socket
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Request, Response

from app.api.errors import DomainError
from app.config import AppSettings
from app.core.llm import LLMProvider, ModelConfig, OpenAICompatibleProvider
from app.core.ollama_validation import normalize_loopback_base_url
from app.core.secrets import SecretStore
from app.remote.credentials import CredentialStore
from app.storage.models import ProviderCredentialState
from app.schemas.ollama import OllamaConfig, RoutingSettings, RuntimeSettingsInput
from app.schemas.settings import (
    MODEL_PRESETS,
    ConnectionBindingView,
    ConnectionTestResult,
    FeishuBindingUpdate,
    ModelConnectionResult,
    ModelSettingsUpdate,
    ModelSettingsView,
    SettingsView,
    WebSearchSettingsUpdate,
    YuqueApiBindingView,
    YuqueApiSettingsUpdate,
)
from app.schemas.web_search import SearchConnectionResult, WebSearchSettings
from app.search.model_native import ModelSearchProvider, detect_native_search
from app.search.searxng import SearxngProvider
from app.search.tavily import TavilyProvider
from app.storage.repositories import SettingStore
from app.yuque.api_gateway import YuqueApiGateway
from app.yuque.credentials import YUQUE_CREDENTIAL_SPEC

MODEL_CONFIG_KEY = "model.config"
MODEL_KEY_REFERENCE = "model.api_key_ref"
MODEL_API_KEY_NAME = "model-api-key"
WEB_SEARCH_CONFIG_KEY = "web-search.config"
WEB_SEARCH_API_KEY_NAME = "web-search:tavily"
OLLAMA_RUNTIME_CONFIG_KEY = "ollama.config"
MODEL_ROUTING_KEY = "model.routing"
YUQUE_API_TOKEN_NAME = "yuque-api:token"
YUQUE_API_VERIFIED_KEY = "yuque-api.verified"
YUQUE_API_LABEL_KEY = "yuque-api.account-label"
FEISHU_WEBHOOK_NAME = "feishu:webhook"
FEISHU_VERIFIED_KEY = "feishu.verified"
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
            runtime=self.runtime(),
            yuque_api=self.yuque_api_binding(),
            feishu=self.feishu_binding(),
        )

    def yuque_api_binding(self) -> YuqueApiBindingView:
        state = self._yuque_api_state()
        if state is not None:
            return YuqueApiBindingView(
                configured=state.configured,
                verified=state.verified,
                label=state.account_label if state.verified else None,
                active=state.verified,
            )
        # Legacy fallback: services constructed without a credential store
        # (unit tests) keep reading the old settings keys.
        configured = bool(self.secret_store.get(YUQUE_API_TOKEN_NAME))
        verified = configured and self.setting_store.get(YUQUE_API_VERIFIED_KEY) == "true"
        label = self.setting_store.get(YUQUE_API_LABEL_KEY) if verified else None
        return YuqueApiBindingView(
            configured=configured,
            verified=verified,
            label=label or None,
            active=verified,
        )

    def _yuque_api_state(self):
        if self.credential_store is None:
            return None
        spec = YUQUE_CREDENTIAL_SPEC.channel("api")
        return self.credential_store.channel_state("yuque", "api", spec)

    def feishu_binding(self) -> ConnectionBindingView:
        if self.credential_store is not None:
            record = self.credential_store.get("feishu", "webhook")
            if record is not None:
                configured = bool(self.credential_store.secret_for("feishu", "webhook"))
                return ConnectionBindingView(
                    configured=configured,
                    verified=configured
                    and record.state == ProviderCredentialState.VERIFIED.value,
                )
        configured = bool(self.secret_store.get(FEISHU_WEBHOOK_NAME))
        verified = configured and self.setting_store.get(FEISHU_VERIFIED_KEY) == "true"
        return ConnectionBindingView(configured=configured, verified=verified)

    async def save_yuque_api(self, update: YuqueApiSettingsUpdate) -> YuqueApiBindingView:
        if update.token is None:
            return self.yuque_api_binding()
        token = update.token.strip()
        if self.credential_store is not None:
            # Unified path: keychain write + state row reset, rolled back
            # together on failure inside the store.
            self.credential_store.save_secret("yuque", "api", token, YUQUE_API_TOKEN_NAME)
            # Legacy mirror so transition-window probes never treat an
            # unverified rotated token as active.
            self.setting_store.set_many(
                {YUQUE_API_VERIFIED_KEY: "false", YUQUE_API_LABEL_KEY: ""}
            )
            return self.yuque_api_binding()
        previous = self.secret_store.get(YUQUE_API_TOKEN_NAME)
        if token:
            self.secret_store.set(YUQUE_API_TOKEN_NAME, token)
        else:
            self.secret_store.delete(YUQUE_API_TOKEN_NAME)
        try:
            values = {YUQUE_API_VERIFIED_KEY: "false", YUQUE_API_LABEL_KEY: ""}
            self.setting_store.set_many(values)
        except Exception:
            if previous is None:
                self.secret_store.delete(YUQUE_API_TOKEN_NAME)
            else:
                self.secret_store.set(YUQUE_API_TOKEN_NAME, previous)
            raise
        return self.yuque_api_binding()

    async def test_yuque_api(self) -> ConnectionTestResult:
        if self.credential_store is not None:
            token = self.credential_store.secret_for("yuque", "api")
        else:
            token = self.secret_store.get(YUQUE_API_TOKEN_NAME)
        if not token:
            raise DomainError(
                "YUQUE_API_TOKEN_REQUIRED",
                "请先保存语雀 API Token",
                400,
                False,
                "保存 Token 后重试",
            )
        try:
            result = await YuqueApiGateway(lambda: token).begin_login()
        except DomainError:
            if self.credential_store is not None:
                self.credential_store.mark_state(
                    "yuque", "api", ProviderCredentialState.UNVERIFIED.value
                )
            raise
        if self.credential_store is not None:
            self.credential_store.mark_state(
                "yuque", "api", ProviderCredentialState.VERIFIED.value,
                account_label=result.account_label,
            )
        self.setting_store.set_many(
            {
                YUQUE_API_VERIFIED_KEY: "true",
                YUQUE_API_LABEL_KEY: result.account_label or "",
            }
        )
        return ConnectionTestResult(
            connected=True,
            message="语雀 API 已连接，后续语雀读写将优先使用 API",
            label=result.account_label,
        )

    async def save_feishu(self, update: FeishuBindingUpdate) -> ConnectionBindingView:
        if update.webhook_url is None:
            return self.feishu_binding()
        webhook_url = _normalize_feishu_webhook(update.webhook_url)
        if self.credential_store is not None:
            self.credential_store.save_secret("feishu", "webhook", webhook_url, FEISHU_WEBHOOK_NAME)
            return self.feishu_binding()
        previous = self.secret_store.get(FEISHU_WEBHOOK_NAME)
        if webhook_url:
            self.secret_store.set(FEISHU_WEBHOOK_NAME, webhook_url)
        else:
            self.secret_store.delete(FEISHU_WEBHOOK_NAME)
        try:
            self.setting_store.set_many({FEISHU_VERIFIED_KEY: "false"})
        except Exception:
            if previous is None:
                self.secret_store.delete(FEISHU_WEBHOOK_NAME)
            else:
                self.secret_store.set(FEISHU_WEBHOOK_NAME, previous)
            raise
        return self.feishu_binding()

    async def test_feishu(self) -> ConnectionTestResult:
        if self.credential_store is not None:
            webhook_url = self.credential_store.secret_for("feishu", "webhook")
        else:
            webhook_url = self.secret_store.get(FEISHU_WEBHOOK_NAME)
        if not webhook_url:
            raise DomainError(
                "FEISHU_WEBHOOK_REQUIRED",
                "请先保存飞书机器人 Webhook",
                400,
                False,
                "保存 Webhook 后重试",
            )
        try:
            await _probe_feishu_webhook(webhook_url)
        except DomainError:
            if self.credential_store is not None:
                self.credential_store.mark_state("feishu", "webhook", "unverified")
            raise
        if self.credential_store is not None:
            self.credential_store.mark_state("feishu", "webhook", "verified")
        self.setting_store.set_many({FEISHU_VERIFIED_KEY: "true"})
        return ConnectionTestResult(
            connected=True,
            message="飞书绑定成功，测试消息已发送",
            label="飞书机器人",
        )

    def runtime(self) -> RuntimeSettingsInput:
        raw_ollama = self.setting_store.get(OLLAMA_RUNTIME_CONFIG_KEY)
        raw_routing = self.setting_store.get(MODEL_ROUTING_KEY)
        try:
            ollama = OllamaConfig.model_validate_json(raw_ollama) if raw_ollama else RuntimeSettingsInput().ollama
            routing = RoutingSettings.model_validate_json(raw_routing) if raw_routing else RuntimeSettingsInput().routing
        except ValueError as error:
            raise DomainError("SETTINGS_INVALID", "运行时设置无效，请重新配置", 500) from error
        return RuntimeSettingsInput(ollama=ollama, routing=routing)

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


def _normalize_feishu_webhook(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        return ""
    parsed = urlparse(candidate)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"open.feishu.cn", "open.larksuite.com"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
        or not parsed.path.startswith("/open-apis/bot/v2/hook/")
    ):
        raise DomainError(
            "FEISHU_WEBHOOK_INVALID",
            "飞书 Webhook 地址无效，请使用飞书自定义机器人的 Webhook",
            422,
        )
    return parsed.geturl()


async def _probe_feishu_webhook(webhook_url: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0), follow_redirects=False) as client:
            response = await client.post(
                webhook_url,
                json={"msg_type": "text", "content": {"text": "DocMind 飞书绑定验证成功"}},
            )
    except httpx.HTTPError as error:
        raise DomainError(
            "FEISHU_UNAVAILABLE", "无法连接飞书，请检查网络后重试", 503, True
        ) from error
    if response.status_code >= 400:
        raise DomainError("FEISHU_AUTH_FAILED", "飞书 Webhook 无效或已失效", 401, False)
    try:
        payload = response.json()
    except ValueError as error:
        raise DomainError("FEISHU_PROTOCOL_ERROR", "飞书返回的数据格式无效", 502, True) from error
    if not isinstance(payload, dict):
        raise DomainError("FEISHU_PROTOCOL_ERROR", "飞书返回的数据格式无效", 502, True)
    code = payload.get("code")
    status_code = payload.get("StatusCode")
    if code not in {None, 0} or status_code not in {None, 0}:
        raise DomainError("FEISHU_AUTH_FAILED", "飞书 Webhook 无效或已失效", 401, False)


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


@router.put("/connections/yuque-api", response_model=SettingsView)
async def save_yuque_api(update: YuqueApiSettingsUpdate, request: Request) -> SettingsView:
    await _service(request).save_yuque_api(update)
    return _service(request).view(_settings(request))


@router.post("/connections/yuque-api/test", response_model=ConnectionTestResult)
async def test_yuque_api(request: Request) -> ConnectionTestResult:
    return await _service(request).test_yuque_api()


@router.put("/connections/feishu", response_model=SettingsView)
async def save_feishu(update: FeishuBindingUpdate, request: Request) -> SettingsView:
    await _service(request).save_feishu(update)
    return _service(request).view(_settings(request))


@router.post("/connections/feishu/test", response_model=ConnectionTestResult)
async def test_feishu(request: Request) -> ConnectionTestResult:
    return await _service(request).test_feishu()
