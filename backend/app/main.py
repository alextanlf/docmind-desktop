from __future__ import annotations

from fastapi import Depends, FastAPI

from app.api.auth import require_runtime_token
from app.api.errors import DomainError, domain_error_handler
from app.config import AppSettings, get_settings
from app.schemas.common import HealthResponse


def create_app(settings: AppSettings | None = None) -> FastAPI:
    runtime_settings = settings or get_settings()
    app = FastAPI(dependencies=[Depends(require_runtime_token)])
    app.dependency_overrides[get_settings] = lambda: runtime_settings
    app.add_exception_handler(DomainError, domain_error_handler)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", version="0.1.0")

    if runtime_settings.environment == "test":

        @app.get("/_test/domain-error")
        async def test_domain_error() -> None:
            raise DomainError("TEST_CONFLICT", "测试冲突", 409)

    return app
