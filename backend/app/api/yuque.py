from __future__ import annotations

from fastapi import APIRouter, Request

from app.schemas.yuque import BrowserInstallResult, LoginResult, LoginStatus
from app.yuque.api_gateway import RoutingYuqueGateway
from app.yuque.gateway import YuqueGateway

router = APIRouter(prefix="/api/yuque", tags=["yuque"])


def _gateway(request: Request) -> YuqueGateway:
    gateway = request.app.state.yuque_gateway
    if isinstance(gateway, RoutingYuqueGateway):
        return request.app.state.yuque_web_gateway
    return gateway


@router.get("/status", response_model=LoginStatus)
async def status(request: Request) -> LoginStatus:
    result = await _gateway(request).login_status()
    _remember_web_connection(request, result.logged_in)
    return result


@router.post("/login", response_model=LoginResult)
async def login(request: Request) -> LoginResult:
    result = await _gateway(request).begin_login()
    _remember_web_connection(request, result.logged_in)
    return result


@router.post("/browser/install", response_model=BrowserInstallResult)
async def install_browser(request: Request) -> BrowserInstallResult:
    return await _gateway(request).install_browser()


def _remember_web_connection(request: Request, connected: bool) -> None:
    settings_service = getattr(request.app.state, "settings_service", None)
    if settings_service is not None:
        settings_service.setting_store.set(
            "yuque-web.connected", "true" if connected else "false"
        )
