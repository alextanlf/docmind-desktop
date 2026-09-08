from __future__ import annotations

from fastapi import APIRouter, Request

from app.schemas.yuque import BrowserInstallResult, LoginResult, LoginStatus
from app.yuque.gateway import YuqueGateway

router = APIRouter(prefix="/api/yuque", tags=["yuque"])


def _gateway(request: Request) -> YuqueGateway:
    return request.app.state.yuque_gateway


@router.get("/status", response_model=LoginStatus)
async def status(request: Request) -> LoginStatus:
    return await _gateway(request).login_status()


@router.post("/login", response_model=LoginResult)
async def login(request: Request) -> LoginResult:
    return await _gateway(request).begin_login()


@router.post("/browser/install", response_model=BrowserInstallResult)
async def install_browser(request: Request) -> BrowserInstallResult:
    return await _gateway(request).install_browser()
