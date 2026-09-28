from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.errors import DomainError
from app.remote.provider import BrowserInstallCapable
from app.remote.registry import ProviderRegistry
from app.schemas.remote import (
    BrowserInstallResult,
    LoginResult,
    LoginStatus,
    ProviderSummaryView,
)

router = APIRouter(prefix="/api/remote", tags=["remote"])


def _registry(request: Request) -> ProviderRegistry:
    return request.app.state.remote_registry  # type: ignore[no-any-return]


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


def _remember_web_connection(request: Request, provider: str, connected: bool) -> None:
    settings_service = getattr(request.app.state, "settings_service", None)
    if settings_service is not None:
        # Legacy mirror kept for the transition window (read by older probes
        # and asserted by existing e2e/API tests).
        settings_service.setting_store.set(
            f"{provider}-web.connected", "true" if connected else "false"
        )
