from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI

from app.api.auth import require_runtime_token
from app.api.embedding import router as embedding_router
from app.api.errors import DomainError, domain_error_handler
from app.api.settings import SettingsService
from app.api.settings import router as settings_router
from app.api.yuque import router as yuque_router
from app.config import AppSettings, get_settings
from app.core.embedding import EmbeddingProvider, create_embedding_provider
from app.core.secrets import KeyringSecretStore, SecretStore
from app.schemas.common import HealthResponse
from app.storage.database import Database
from app.storage.repositories import ImportJobStore, SettingStore
from app.yuque.gateway import PlaywrightYuqueGateway, YuqueGateway


def create_app(
    settings: AppSettings | None = None,
    secret_store: SecretStore | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    yuque_gateway: YuqueGateway | None = None,
) -> FastAPI:
    runtime_settings = settings or get_settings()
    runtime_secret_store = secret_store or KeyringSecretStore()
    runtime_embedding_provider = embedding_provider or create_embedding_provider(
        runtime_settings.embedding_settings
    )
    runtime_yuque_gateway = yuque_gateway or PlaywrightYuqueGateway(runtime_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database_path = Path(runtime_settings.data_dir) / "database" / "docmind.sqlite3"
        database = Database(f"sqlite+pysqlite:///{database_path}")
        database.upgrade()
        ImportJobStore(database).recover_interrupted()
        app.state.database = database
        app.state.settings_service = SettingsService(
            SettingStore(database), runtime_secret_store
        )
        try:
            yield
        finally:
            await runtime_yuque_gateway.close()
            database.engine.dispose()

    app = FastAPI(dependencies=[Depends(require_runtime_token)], lifespan=lifespan)
    app.dependency_overrides[get_settings] = lambda: runtime_settings
    app.state.settings = runtime_settings
    app.state.secret_store = runtime_secret_store
    app.state.embedding_provider = runtime_embedding_provider
    app.state.embedding_prepare_task = None
    app.state.yuque_gateway = runtime_yuque_gateway
    app.add_exception_handler(DomainError, domain_error_handler)
    app.include_router(settings_router)
    app.include_router(embedding_router)
    app.include_router(yuque_router)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", version="0.1.0")

    if runtime_settings.environment == "test":

        @app.get("/_test/domain-error")
        async def test_domain_error() -> None:
            raise DomainError("TEST_CONFLICT", "测试冲突", 409)

    return app
