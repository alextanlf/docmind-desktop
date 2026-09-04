from __future__ import annotations

import asyncio
import json
import os
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from threading import Lock

import httpx
from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError

from app.api.auth import require_runtime_token
from app.api.chat import router as chat_router
from app.api.documents import recover_document_mutations, save_distillation_document
from app.api.documents import router as documents_router
from app.api.embedding import router as embedding_router
from app.api.errors import DomainError, domain_error_handler, request_validation_handler
from app.api.import_batches import router as import_batches_router
from app.api.imports import router as imports_router
from app.api.memory import router as memory_router
from app.api.repositories import router as repositories_router
from app.api.request_limits import RequestBodyLimitMiddleware
from app.api.search import router as search_router
from app.api.sessions import router as sessions_router
from app.api.settings import SettingsService
from app.api.settings import router as settings_router
from app.api.web_search import router as web_search_router
from app.api.yuque import router as yuque_router
from app.chat.service import ChatService
from app.config import AppSettings, get_settings
from app.core.embedding import EmbeddingProvider, FakeEmbeddingProvider, create_embedding_provider
from app.core.llm import (
    ChatDelta,
    ChatRequest,
    LLMProvider,
    ModelConfig,
    ModelConnectionResult,
    OpenAICompatibleProvider,
)
from app.core.retrieval import HybridRetriever
from app.core.secrets import KeyringSecretStore, MemorySecretStore, SecretStore
from app.document.chunker import SemanticChunker
from app.document.parser import DocumentParser
from app.document.safe_http import SafeHttpClient
from app.document.sources import SourceInspector
from app.document.web_discovery import WebDiscovery
from app.imports.batch_service import BatchService
from app.imports.events import InMemoryEventBroker
from app.imports.service import ImportService
from app.memory.distillation import DistillationService
from app.memory.indexer import MemoryIndexer
from app.memory.persistence import LocalKnowledgeStore
from app.memory.retriever import MemoryRetriever
from app.memory.summary import SummaryScheduler, SummaryService
from app.schemas.common import HealthResponse
from app.search.service import SearchService
from app.search.tavily import TavilyProvider
from app.storage.database import Database
from app.storage.repositories import (
    BatchImportStore,
    ConversationStore,
    CrawlEntryStore,
    DocumentMutationStore,
    DocumentStore,
    ImportJobStore,
    MemoryStore,
    RepositoryStore,
    SettingStore,
    VectorCleanupStore,
    WebSearchRunStore,
)
from app.storage.vectorstore import PersistentVectorStore
from app.yuque.discovery import YuqueDiscovery
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
    fake_services = os.getenv("DOCMIND_FAKE_SERVICES") == "1"
    if fake_services and runtime_settings.environment == "production":
        raise ValueError("fake services are not allowed in production")
    if fake_services:
        from app.testing.fakes import (
            E2EControl,
            E2EControlledFakeEmbeddingProvider,
            FakeLLMProvider,
            FakeYuqueGateway,
        )

        e2e_control = E2EControl(runtime_settings.data_dir) if os.getenv("DOCMIND_E2E") == "1" else None
        runtime_secret_store = secret_store or MemorySecretStore()
        runtime_embedding_provider = embedding_provider or (
            E2EControlledFakeEmbeddingProvider(
                runtime_settings.embedding_settings, control=e2e_control
            )
            if e2e_control is not None
            else FakeEmbeddingProvider(runtime_settings.embedding_settings)
        )
        runtime_yuque_gateway = yuque_gateway or FakeYuqueGateway()
        fake_llm_provider: LLMProvider | None = llm_provider or FakeLLMProvider(control=e2e_control)
    else:
        runtime_secret_store = secret_store or KeyringSecretStore()
        runtime_embedding_provider = embedding_provider or create_embedding_provider(
            runtime_settings.embedding_settings
        )
        runtime_yuque_gateway = yuque_gateway or PlaywrightYuqueGateway(runtime_settings)
        fake_llm_provider = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database_path = Path(runtime_settings.data_dir) / "database" / "docmind.sqlite3"
        database = Database(f"sqlite+pysqlite:///{database_path}")
        database.upgrade()
        ImportJobStore(database).recover_interrupted()
        app.state.database = database
        if fake_llm_provider is None:
            app.state.settings_service = SettingsService(
                SettingStore(database), runtime_secret_store
            )
        else:
            app.state.settings_service = SettingsService(
                SettingStore(database),
                runtime_secret_store,
                provider_factory=lambda _config, _api_key: fake_llm_provider,
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
        app.state.batch_store = BatchImportStore(database)
        class _SystemResolver:
            async def resolve(self, host: str):
                infos = await asyncio.to_thread(socket.getaddrinfo, host, None, type=socket.SOCK_STREAM)
                return list({info[4][0] for info in infos})
        web_discovery = WebDiscovery(
            SafeHttpClient(resolver=_SystemResolver(), transport=httpx.AsyncHTTPTransport()),
            runtime_settings.staging_dir,
            frontier=CrawlEntryStore(database),
        )
        yuque_discovery = YuqueDiscovery(
            runtime_yuque_gateway, repository_store, runtime_settings.staging_dir
        )
        app.state.batch_service = BatchService(
            store=app.state.batch_store,
            import_service=app.state.import_service,
            document_store=document_store,
            event_broker=InMemoryEventBroker(retention=100),
            staging_root=runtime_settings.staging_dir,
            manifest_max_bytes=runtime_settings.staging_manifest_max_bytes,
            batch_max_items=runtime_settings.batch_max_items,
            web_discovery=web_discovery,
            yuque_discovery=yuque_discovery,
        )
        app.state.batch_service.recover_on_startup()
        class _LazyTavily:
            async def search(self, req):
                try:
                    key = runtime_secret_store.get("web-search:tavily")
                except (DomainError, OSError):
                    key = None
                if not key:
                    raise RuntimeError("search key unavailable")
                return await TavilyProvider(key).search(req)
        app.state.search_service = SearchService(_LazyTavily(), WebSearchRunStore(database), runtime_secret_store)
        runtime_llm_provider = fake_llm_provider or _RuntimeLLMProvider(
            app.state.settings_service,
            runtime_secret_store,
        )
        app.state.repository_store = repository_store
        app.state.document_store = document_store
        app.state.import_job_store = ImportJobStore(database)
        app.state.vector_cleanup_store = VectorCleanupStore(database)
        app.state.document_mutation_store = DocumentMutationStore(database)
        app.state.vector_store = vector_store
        app.state.document_parser = DocumentParser()
        app.state.document_chunker = SemanticChunker()
        await recover_document_mutations(app)
        for cleanup in app.state.vector_cleanup_store.list():
            with suppress(Exception):
                pending_ids = list(json.loads(cleanup.vector_ids_json))
                owned_ids = document_store.owned_vector_ids(pending_ids)
                deletable_ids = [
                    identifier for identifier in pending_ids if identifier not in owned_ids
                ]
                if deletable_ids:
                    await asyncio.to_thread(
                        vector_store.delete, cleanup.repository_id, deletable_ids
                    )
                app.state.vector_cleanup_store.delete(cleanup.id)
        app.state.conversation_store = conversation_store
        memory_store = MemoryStore(database)
        memory_store.recover_interrupted()
        memory_indexer = MemoryIndexer(database, runtime_embedding_provider, vector_store)
        memory_retriever = MemoryRetriever(database, runtime_embedding_provider, vector_store)
        await memory_indexer.replay_cleanups()
        await memory_indexer.replay_pending_indexes()
        summary_service = SummaryService(
            conversation_store,
            memory_store,
            llm=runtime_llm_provider,
            indexer=memory_indexer,
        )
        summary_scheduler = SummaryScheduler(summary_service)
        app.state.summary_scheduler = summary_scheduler
        app.state.memory_store = memory_store
        app.state.memory_indexer = memory_indexer
        app.state.memory_retriever = memory_retriever
        app.state.summary_service = summary_service
        app.state.distillation_event_broker = InMemoryEventBroker(retention=None)
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
            message_activity_callback=lambda session_id: summary_service.record_message_activity(session_id),
            memory_retriever=memory_retriever,
            search_service=app.state.search_service,
            settings_service=app.state.settings_service,
        )
        app.state.chat_service = chat_service
        app.state.distillation_service = DistillationService(
            conversation_store,
            runtime_llm_provider,
            LocalKnowledgeStore(runtime_settings.data_dir),
            yuque_gateway=runtime_yuque_gateway,
            mutation_store=app.state.document_mutation_store,
            indexer=memory_indexer,
            document_saver=lambda **kwargs: save_distillation_document(app, **kwargs),
            event_broker=app.state.distillation_event_broker,
        )
        summary_task = asyncio.create_task(summary_scheduler.run())
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
            summary_scheduler.stop()
            await summary_task
            await runtime_yuque_gateway.close()
            database.engine.dispose()

    app = FastAPI(dependencies=[Depends(require_runtime_token)], lifespan=lifespan)
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_bytes=runtime_settings.max_request_body_bytes,
    )
    app.dependency_overrides[get_settings] = lambda: runtime_settings
    app.state.settings = runtime_settings
    app.state.secret_store = runtime_secret_store
    app.state.embedding_provider = runtime_embedding_provider
    app.state.embedding_prepare_task = None
    app.state.yuque_gateway = runtime_yuque_gateway
    app.state.fake_llm_provider = fake_llm_provider
    app.state.import_tasks = set()
    app.state.active_document_mutations = set()
    app.state.document_mutation_registry_lock = Lock()
    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(RequestValidationError, request_validation_handler)
    app.include_router(settings_router)
    app.include_router(embedding_router)
    app.include_router(yuque_router)
    app.include_router(imports_router)
    app.include_router(import_batches_router)
    app.include_router(repositories_router)
    app.include_router(documents_router)
    app.include_router(sessions_router)
    app.include_router(chat_router)
    app.include_router(search_router)
    app.include_router(web_search_router)
    app.include_router(memory_router)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", version="0.1.0")

    if runtime_settings.environment == "test":

        @app.get("/_test/domain-error")
        async def test_domain_error() -> None:
            raise DomainError("TEST_CONFLICT", "测试冲突", 409)

    return app
