from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError

from app.api.auth import require_runtime_token
from app.api.chat import router as chat_router
from app.api.documents import router as documents_router
from app.api.embedding import router as embedding_router
from app.api.errors import DomainError, domain_error_handler, request_validation_handler
from app.api.imports import router as imports_router
from app.api.repositories import router as repositories_router
from app.api.sessions import router as sessions_router
from app.api.settings import SettingsService
from app.api.settings import router as settings_router
from app.api.yuque import router as yuque_router
from app.chat.service import ChatService
from app.config import AppSettings, get_settings
from app.core.embedding import EmbeddingProvider, create_embedding_provider
from app.core.llm import (
    ChatDelta,
    ChatRequest,
    LLMProvider,
    ModelConfig,
    ModelConnectionResult,
    OpenAICompatibleProvider,
)
from app.core.retrieval import HybridRetriever
from app.core.secrets import KeyringSecretStore, SecretStore
from app.document.chunker import SemanticChunker
from app.document.parser import DocumentParser
from app.document.sources import SourceInspector
from app.imports.events import InMemoryEventBroker
from app.imports.service import ImportService
from app.schemas.common import HealthResponse
from app.storage.database import Database
from app.storage.repositories import (
    ConversationStore,
    DocumentStore,
    ImportJobStore,
    RepositoryStore,
    SettingStore,
)
from app.storage.vectorstore import PersistentVectorStore
from app.yuque.gateway import PlaywrightYuqueGateway, YuqueGateway


class _RuntimeLLMProvider:
    def __init__(
        self, settings_service: SettingsService, secret_store: SecretStore
    ) -> None:
        self.settings_service = settings_service
        self.secret_store = secret_store

    async def test_connection(self) -> ModelConnectionResult:
        return await self._provider().test_connection()

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatDelta]:
        async for delta in self._provider().stream_chat(request):
            yield delta

    def _provider(self) -> LLMProvider:
        api_key = self.secret_store.get("model-api-key")
        if not api_key:
            raise DomainError(
                "MODEL_AUTH_FAILED",
                "请先配置 API Key",
                400,
                False,
                "保存 API Key 后重试",
            )
        return OpenAICompatibleProvider(
            ModelConfig(**self.settings_service.model().model_dump()), api_key
        )


def create_app(
    settings: AppSettings | None = None,
    secret_store: SecretStore | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    yuque_gateway: YuqueGateway | None = None,
    llm_provider: LLMProvider | None = None,
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
        repository_store = RepositoryStore(database)
        conversation_store = ConversationStore(database)
        vector_store = PersistentVectorStore(runtime_settings.vectorstore_settings)
        document_store = DocumentStore(database)
        app.state.import_service = ImportService(
            settings=runtime_settings,
            source_inspector=SourceInspector(runtime_settings),
            parser=DocumentParser(),
            chunker=SemanticChunker(),
            embedding_provider=runtime_embedding_provider,
            vector_store=vector_store,
            yuque_gateway=runtime_yuque_gateway,
            repository_store=repository_store,
            document_store=document_store,
            job_store=ImportJobStore(database),
            event_broker=InMemoryEventBroker(),
        )
        runtime_llm_provider = llm_provider or _RuntimeLLMProvider(
            app.state.settings_service,
            runtime_secret_store,
        )
        app.state.repository_store = repository_store
        app.state.document_store = document_store
        app.state.vector_store = vector_store
        app.state.document_parser = DocumentParser()
        app.state.document_chunker = SemanticChunker()
        app.state.conversation_store = conversation_store
        chat_service = ChatService(
            retriever=HybridRetriever(
                database=database,
                vector_store=vector_store,
                embedding_provider=runtime_embedding_provider,
                similarity_threshold=runtime_settings.rag_similarity_threshold,
            ),
            llm=runtime_llm_provider,
            conversation_store=conversation_store,
            event_broker=InMemoryEventBroker(retention=None),
        )
        app.state.chat_service = chat_service
        await app.state.import_service.recover_pending_vector_cleanup()
        try:
            yield
        finally:
            tasks = list(app.state.import_tasks)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await chat_service.stop()
            await runtime_yuque_gateway.close()
            database.engine.dispose()

    app = FastAPI(dependencies=[Depends(require_runtime_token)], lifespan=lifespan)
    app.dependency_overrides[get_settings] = lambda: runtime_settings
    app.state.settings = runtime_settings
    app.state.secret_store = runtime_secret_store
    app.state.embedding_provider = runtime_embedding_provider
    app.state.embedding_prepare_task = None
    app.state.yuque_gateway = runtime_yuque_gateway
    app.state.import_tasks = set()
    app.state.pending_vector_cleanup = {}
    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(RequestValidationError, request_validation_handler)
    app.include_router(settings_router)
    app.include_router(embedding_router)
    app.include_router(yuque_router)
    app.include_router(imports_router)
    app.include_router(repositories_router)
    app.include_router(documents_router)
    app.include_router(sessions_router)
    app.include_router(chat_router)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", version="0.1.0")

    if runtime_settings.environment == "test":

        @app.get("/_test/domain-error")
        async def test_domain_error() -> None:
            raise DomainError("TEST_CONFLICT", "测试冲突", 409)

    return app
