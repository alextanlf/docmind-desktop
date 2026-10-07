from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.errors import DomainError
from app.remote.credentials import CredentialChannelSpec, CredentialStore, ProviderCredentialSpec
from app.remote.provider import BrowserInstallCapable
from app.remote.registry import ProviderRegistry
from app.schemas.remote import (
    BrowserInstallResult,
    CredentialChannelView,
    CredentialTestResult,
    LoginResult,
    LoginStatus,
    ProviderSummaryView,
    SaveCredentialRequest,
)

router = APIRouter(prefix="/api/remote", tags=["remote"])


def _registry(request: Request) -> ProviderRegistry:
    return request.app.state.remote_registry  # type: ignore[no-any-return]


def _credential_store(request: Request) -> CredentialStore:
    store = getattr(request.app.state, "credential_store", None)
    if store is None:
        raise DomainError(
            "REMOTE_CREDENTIALS_UNAVAILABLE",
            "远程凭据服务不可用",
            503,
            True,
            "稍后重试",
        )
    return store  # type: ignore[no-any-return]


def _channel_spec(request: Request, provider: str, channel: str) -> CredentialChannelSpec:
    registry = _registry(request)
    registry.get(provider)  # raises REMOTE_PROVIDER_UNKNOWN for unknown names
    spec: ProviderCredentialSpec | None = registry.credential_spec(provider)
    channel_spec = spec.channel(channel) if spec else None
    if channel_spec is None:
        raise DomainError(
            "REMOTE_CAPABILITY_UNSUPPORTED",
            f"该远程来源不支持 {channel} 凭据通道",
            400,
            False,
        )
    return channel_spec


def _channel_view(
    store: CredentialStore, provider: str, spec: CredentialChannelSpec
) -> CredentialChannelView:
    state = store.channel_state(provider, spec.name, spec)
    return CredentialChannelView(
        provider=state.provider,
        channel=state.channel,
        label=state.label,
        configured=state.configured,
        state=state.state,
        account_label=state.account_label,
        has_secret=state.has_secret,
        secret_placeholder=spec.secret_placeholder,
        help_url=spec.help_url,
        help_label=spec.help_label,
        purpose=spec.purpose,
        hint=spec.hint,
    )


@router.get("/providers", response_model=list[ProviderSummaryView])
async def list_providers(request: Request) -> list[ProviderSummaryView]:
    return _registry(request).summaries()


@router.get("/providers/{provider}/status", response_model=LoginStatus)
async def status(request: Request, provider: str) -> LoginStatus:
    result = await _registry(request).get(provider).login_status()
    _remember_web_connection(request, provider, result.logged_in)
    return result


@router.post("/providers/{provider}/login", response_model=LoginResult)
async def login(request: Request, provider: str) -> LoginResult:
    result = await _registry(request).get(provider).begin_login()
    _remember_web_connection(request, provider, result.logged_in)
    return result


@router.post("/providers/{provider}/browser/install", response_model=BrowserInstallResult)
async def install_browser(request: Request, provider: str) -> BrowserInstallResult:
    remote = _registry(request).get(provider)
    if not isinstance(remote, BrowserInstallCapable):
        raise DomainError(
            "REMOTE_CAPABILITY_UNSUPPORTED",
            "该远程来源不支持安装登录浏览器",
            400,
            False,
        )
    result = await remote.install_browser()
    return result  # type: ignore[return-value]


# --- Unified credential management ----------------------------------------


@router.get("/providers/{provider}/credentials", response_model=list[CredentialChannelView])
async def list_credentials(request: Request, provider: str) -> list[CredentialChannelView]:
    registry = _registry(request)
    registry.get(provider)  # 404 for unknown providers
    spec = registry.credential_spec(provider)
    if spec is None:
        return []
    store = _credential_store(request)
    return [_channel_view(store, provider, channel) for channel in spec.channels]


@router.put("/providers/{provider}/credentials/{channel}", response_model=CredentialChannelView)
async def save_credential(
    request: Request, provider: str, channel: str, body: SaveCredentialRequest
) -> CredentialChannelView:
    spec = _channel_spec(request, provider, channel)
    if not spec.has_secret:
        raise DomainError(
            "REMOTE_CAPABILITY_UNSUPPORTED",
            "该凭据通道不需要密钥",
            400,
            False,
        )
    store = _credential_store(request)
    secret_ref = spec.default_secret_ref or f"{provider}:{channel}"
    # The channel owns its own validation rules. A constrained secret (e.g. a
    # webhook URL whose host whitelist is an SSRF guard) declares a normalizer
    # in its spec, so the API layer never special-cases a provider name.
    secret = spec.normalizer(body.secret) if spec.normalizer is not None else body.secret.strip()
    store.save_secret(provider, channel, secret, secret_ref)
    return _channel_view(store, provider, spec)


@router.post(
    "/providers/{provider}/credentials/{channel}/test", response_model=CredentialTestResult
)
async def test_credential(request: Request, provider: str, channel: str) -> CredentialTestResult:
    spec = _channel_spec(request, provider, channel)
    if spec.tester is None:
        raise DomainError(
            "REMOTE_CAPABILITY_UNSUPPORTED",
            "该凭据通道不支持连接测试",
            400,
            False,
        )
    store = _credential_store(request)
    secret = store.secret_for(provider, channel)
    if not secret:
        raise DomainError(
            "REMOTE_CREDENTIAL_REQUIRED",
            f"请先保存{spec.label}凭据",
            400,
            False,
            "保存凭据后重试",
        )
    try:
        label = await spec.tester(secret)
    except DomainError:
        store.mark_state(provider, channel, "unverified")
        raise
    except Exception as error:
        store.mark_state(provider, channel, "unverified")
        raise DomainError(
            "REMOTE_OPERATION_FAILED",
            "凭据验证失败，请稍后重试",
            502,
            True,
        ) from error
    store.mark_state(provider, channel, "verified", account_label=label)
    return CredentialTestResult(
        connected=True,
        message=f"{spec.label}已连接",
        label=label,
    )


@router.delete("/providers/{provider}/credentials/{channel}", response_model=CredentialChannelView)
async def delete_credential(request: Request, provider: str, channel: str) -> CredentialChannelView:
    spec = _channel_spec(request, provider, channel)
    store = _credential_store(request)
    store.clear(provider, channel)
    return _channel_view(store, provider, spec)


def _remember_web_connection(request: Request, provider: str, connected: bool) -> None:
    registry = _registry(request)
    spec = registry.credential_spec(provider)
    if spec is None or spec.channel(spec.login_channel) is None:
        return
    store = getattr(request.app.state, "credential_store", None)
    if store is not None:
        store.mark_state(provider, spec.login_channel, "verified" if connected else "disconnected")
