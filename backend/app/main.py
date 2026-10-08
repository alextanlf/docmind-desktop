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
from app.api.embedding import consume_terminal_exception
from app.api.embedding import router as embedding_router
from app.api.errors import DomainError, domain_error_handler, request_validation_handler
from app.api.graph import router as graph_router
from app.api.import_batches import router as import_batches_router
from app.api.imports import router as imports_router
from app.api.local_model import router as local_model_router
from app.api.memory import router as memory_router
from app.api.plugins import router as plugins_router
from app.api.remote import router as remote_router
from app.api.repositories import router as repositories_router
from app.api.request_limits import RequestBodyLimitMiddleware
from app.api.search import router as search_router
from app.api.sessions import router as sessions_router
from app.api.settings import SettingsService
from app.api.settings import router as settings_router
from app.api.sync import router as sync_router
from app.api.web_search import router as web_search_router
from app.chat.service import ChatService
from app.chat.tools import ToolRegistry, WebSearchTool
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
from app.core.local_model import LocalModelProvider
from app.core.local_model_service import LocalModelService
from app.core.model_router import ModelRouter
from app.core.retrieval import HybridRetriever
from app.core.secrets import KeyringSecretStore, MemorySecretStore, SecretStore
from app.document.builtin_formats import builtin_registry
from app.document.chunker import SemanticChunker
from app.document.parser import DocumentParser
from app.document.safe_http import SafeHttpClient
from app.document.sources import SourceInspector
from app.document.web_discovery import WebDiscovery
from app.feishu.credentials import (
    FEISHU_CREDENTIAL_SPEC,
    build_feishu_notification_target,
)
from app.feishu.provider import FeishuProvider
from app.feishu.tokens import FeishuTokenManager
from app.imports.batch_service import BatchService
from app.imports.events import InMemoryEventBroker
from app.imports.service import ImportService
from app.memory.distillation import DistillationService
from app.memory.indexer import MemoryIndexer
from app.memory.persistence import LocalKnowledgeStore
from app.memory.retriever import MemoryRetriever
from app.memory.summary import SummaryScheduler, SummaryService
from app.plugins.catalog import PluginCatalog
from app.plugins.contributions import (
    PluginHost,
    RemoteSourceContribution,
    install_contribution,
)
from app.plugins.loader import load_plugins
from app.remote.credentials import CredentialStore
from app.remote.discovery import RemoteDiscovery
from app.remote.notifications import NotificationHub
from app.remote.provider import RemoteProvider
from app.remote.registry import ProviderRegistry
from app.remote.snapshot import read_remote_snapshot
from app.schemas.common import HealthResponse
from app.search.fallback import FallbackSearchProvider
from app.search.model_native import ModelSearchProvider
from app.search.query_planner import LLMQueryPlanner
from app.search.service import SearchService
from app.search.tavily import TAVILY_SECRET_NAME, TavilyProvider
from app.storage.database import Database
from app.storage.models import ProviderCredentialState
from app.storage.repositories import (
    BatchImportStore,
    ConversationStore,
    CrawlEntryStore,
    DocumentMutationStore,
    DocumentStore,
    EmbeddingRebuildStore,
    GraphStore,
    ImportJobStore,
    MemoryStore,
    RepositoryStore,
    RepositorySyncStateStore,
    SettingStore,
    VectorCleanupStore,
    VersionStore,
    WebSearchRunStore,
)
from app.storage.vectorstore import PersistentVectorStore
from app.sync.conflict import SyncConflictService
from app.sync.refresher import DocumentRefresher
from app.sync.scheduler import SyncScheduler
from app.sync.service import IncrementalSyncService
from app.yuque.api_gateway import YuqueApiGateway, YuqueProvider
from app.yuque.credentials import YUQUE_CREDENTIAL_SPEC
from app.yuque.wd_gateway import WebDriverYuqueGateway

# 界面启动时会拉起语雀浏览器做"首次设置"检查；启动同步必须排在它后面，
# 否则用户会看到几十秒的转圈。
SYNC_STARTUP_DELAY_SECONDS = 5.0


class _RuntimeLLMProvider:
    def __init__(self, settings_service: SettingsService, secret_store: SecretStore) -> None:
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


class _RoutedLLMProvider:
    def __init__(self, router: ModelRouter) -> None:
        self.router, self.last_route = router, None

    async def test_connection(self):
        return await self.router.cloud.test_connection()

    async def open_stream(self, request):
        routed = await self.router.open_stream(request)
        self.last_route = routed.route
        return routed

    async def stream_chat(self, request):
        routed = await self.open_stream(request)
        async for delta in routed.deltas:
            yield delta


def _production_contributions(
    credential_store: CredentialStore,
    runtime_settings: AppSettings,
) -> tuple[RemoteSourceContribution, ...]:
    """The built-in contributions, with their configuration probes.

    Declared as contributions rather than registered directly, so the built-ins
    and third-party plugins go through the identical install path — there is no
    "already registered" shortcut for our own integrations.
    """

    def _yuque_api_token() -> str | None:
        record = credential_store.get("yuque", "api")
        if record is None or record.state != ProviderCredentialState.VERIFIED.value:
            return None
        return credential_store.secret_for("yuque", "api")

    def _yuque_configured() -> bool:
        return credential_store.any_verified("yuque", ("web", "api"))

    return (
        RemoteSourceContribution(
            provider=YuqueProvider(
                WebDriverYuqueGateway(runtime_settings),
                YuqueApiGateway(_yuque_api_token),
                lambda: _yuque_api_token() is not None,
            ),
            credential_spec=YUQUE_CREDENTIAL_SPEC,
            is_configured=_yuque_configured,
        ),
        # Feishu: availability is derived from the credential spec (app or user
        # channel verified) by the registry's default probe.
        RemoteSourceContribution(
            provider=FeishuProvider(FeishuTokenManager(credential_store)),
            credential_spec=FEISHU_CREDENTIAL_SPEC,
        ),
    )


def _injected_registry(
    providers: dict[str, RemoteProvider] | None,
) -> ProviderRegistry | None:
    """Build a registry from explicitly injected providers, if any.

    Tests and harnesses inject their own providers; production and fake-services
    mode assemble the registry inside the lifespan where the database-backed
    configuration probe exists.
    """
    if providers is None:
        return None
    registry = ProviderRegistry()
    for provider in providers.values():
        registry.register(provider, always_configured=True)
    return registry


def create_app(
    settings: AppSettings | None = None,
    secret_store: SecretStore | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    providers: dict[str, RemoteProvider] | None = None,
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
            FakeRemoteProvider,
        )

        e2e_control = (
            E2EControl(runtime_settings.data_dir) if os.getenv("DOCMIND_E2E") == "1" else None
        )
        runtime_secret_store = secret_store or MemorySecretStore(str(runtime_settings.data_dir))
        runtime_embedding_provider = embedding_provider or (
            E2EControlledFakeEmbeddingProvider(
                runtime_settings.embedding_settings, control=e2e_control
            )
            if e2e_control is not None
            else FakeEmbeddingProvider(runtime_settings.embedding_settings)
        )
        fake_llm_provider: LLMProvider | None = llm_provider or FakeLLMProvider(control=e2e_control)
    else:
        runtime_secret_store = secret_store or KeyringSecretStore()
        runtime_embedding_provider = embedding_provider or create_embedding_provider(
            runtime_settings.embedding_settings
        )
        fake_llm_provider = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database_path = Path(runtime_settings.data_dir) / "database" / "docmind.sqlite3"
        database = Database(f"sqlite+pysqlite:///{database_path}")
        database.upgrade()

        def _configured_local_base_url() -> str:
            # Read lazily: this service is constructed before SettingsService, and
            # the user can change the address at any time. Resolving per access
            # keeps status/preflight on the same host as inference.
            service = getattr(app.state, "settings_service", None)
            if service is None:
                return ""
            return service.runtime().local.base_url

        app.state.local_model_service = LocalModelService(
            base_url_provider=_configured_local_base_url
        )
        ImportJobStore(database).recover_interrupted()
        app.state.database = database
        credential_store = CredentialStore(database, runtime_secret_store)
        app.state.credential_store = credential_store
        if fake_llm_provider is None:
            app.state.settings_service = SettingsService(
                SettingStore(database),
                runtime_secret_store,
                credential_store=credential_store,
            )
        else:
            app.state.settings_service = SettingsService(
                SettingStore(database),
                runtime_secret_store,
                provider_factory=lambda _config, _api_key: fake_llm_provider,
                credential_store=credential_store,
            )
        repository_store = RepositoryStore(database)
        # One format registry for the whole process: the import service, the
        # directory scanner, the downloader and the standalone parser all read
        # it, so a format a plugin contributes is importable everywhere at once
        # instead of only on the path that happened to be handed the registry.
        formats = builtin_registry()
        app.state.format_registry = formats
        notifications = NotificationHub()
        if not fake_services:
            notifications.register(build_feishu_notification_target(credential_store))
            # Built-ins and third-party plugins install onto the *same* host, so
            # they flow through identical credential / discovery / notification
            # paths and the settings page cannot tell them apart. A plugin that
            # fails to install is recorded and skipped: one broken integration
            # must not stop the app from starting.
            app.state.notifications = notifications

        registry = app.state.remote_registry
        # A pre-built registry means providers were injected by a test or a
        # harness; installing the built-ins on top of it would collide with
        # whatever it was assembled from.
        registry_injected = registry is not None
        if registry is None:
            registry = ProviderRegistry(credential_store)
            if fake_services:
                registry.register(
                    FakeRemoteProvider(runtime_settings.data_dir), always_configured=True
                )
            app.state.remote_registry = registry

        host = PluginHost(
            providers=registry,
            formats=formats,
            credentials=credential_store,
            notifications=None if fake_services else notifications,
        )
        if fake_services:
            # Fake mode installs no built-ins and skips discovery, so tests see
            # a deterministic catalogue.
            app.state.plugin_diagnostics = None
        else:
            if not registry_injected:
                for contribution in _production_contributions(
                    credential_store, runtime_settings
                ):
                    install_contribution(host, contribution)
            app.state.plugin_diagnostics = load_plugins(host, runtime_settings.plugins_dir)
        host_notifications = host.notifications
        app.state.plugin_host = host
        # The plugin page is a read model over the installed contributions, so
        # there is no second catalogue to keep in sync — installing one is
        # enough to make its card appear.
        app.state.plugin_catalog = PluginCatalog(host)
        if host_notifications is not None:
            app.state.notifications = host_notifications
        conversation_store = ConversationStore(database)
        vector_store = PersistentVectorStore(runtime_settings.vectorstore_settings)
        document_store = DocumentStore(database)
        document_parser = DocumentParser(formats)
        app.state.document_parser = document_parser
        app.state.import_service = ImportService(
            settings=runtime_settings,
            source_inspector=SourceInspector(runtime_settings, formats=formats),
            parser=document_parser,
            chunker=SemanticChunker(),
            embedding_provider=runtime_embedding_provider,
            vector_store=vector_store,
            remote_registry=registry,
            repository_store=repository_store,
            document_store=document_store,
            job_store=ImportJobStore(database),
            event_broker=InMemoryEventBroker(),
            notifications=notifications,
        )
        app.state.batch_store = BatchImportStore(database)

        class _SystemResolver:
            async def resolve(self, host: str):
                infos = await asyncio.to_thread(
                    socket.getaddrinfo, host, None, type=socket.SOCK_STREAM
                )
                return list({info[4][0] for info in infos})

        safe_http_client = SafeHttpClient(
            resolver=_SystemResolver(), transport=httpx.AsyncHTTPTransport()
        )
        web_discovery = WebDiscovery(
            safe_http_client,
            runtime_settings.staging_dir,
            frontier=CrawlEntryStore(database),
        )
        remote_discovery = RemoteDiscovery(registry, repository_store, runtime_settings.staging_dir)
        app.state.batch_service = BatchService(
            store=app.state.batch_store,
            import_service=app.state.import_service,
            document_store=document_store,
            event_broker=InMemoryEventBroker(retention=100),
            staging_root=runtime_settings.staging_dir,
            manifest_max_bytes=runtime_settings.staging_manifest_max_bytes,
            batch_max_items=runtime_settings.batch_max_items,
            web_discovery=web_discovery,
            remote_discovery=remote_discovery,
            formats=formats,
        )
        app.state.batch_service.recover_on_startup()

        def _read_secret(name: str) -> str | None:
            try:
                return runtime_secret_store.get(name)
            except (DomainError, OSError):
                return None

        def _model_credentials() -> tuple[ModelConfig, str | None]:
            try:
                routing_mode = app.state.settings_service.runtime().routing.mode
            except DomainError:
                routing_mode = "cloud_only"
            if routing_mode == "local_only":
                return app.state.settings_service.model(), None
            return app.state.settings_service.model(), _read_secret("model-api-key")

        class _LazyTavily:
            name = "tavily"

            def available(self) -> bool:
                # 免密钥即可用，所以恒定可用 —— 不再有"有没有配 Key"这个前置条件。
                return True

            async def search(self, req):
                # Key 是惰性读取的：历史配过的 Key 仍然生效（付费档不限流），
                # 只是设置页已删除、不再有输入口。
                return await TavilyProvider(_read_secret(TAVILY_SECRET_NAME)).search(req)

            async def test_connection(self):
                return await TavilyProvider(_read_secret(TAVILY_SECRET_NAME)).test_connection()

        cloud_llm_provider = fake_llm_provider or _RuntimeLLMProvider(
            app.state.settings_service,
            runtime_secret_store,
        )

        def _current_local_config():
            return app.state.settings_service.runtime().local

        def _current_local_provider() -> LLMProvider:
            return LocalModelProvider(config=_current_local_config())

        if app.state.settings_service.runtime().routing.mode == "cloud_only":
            runtime_llm_provider = cloud_llm_provider
        else:
            # `runtime_reader` matters here: without it the router keeps the
            # routing mode and model captured at construction, so switching from
            # "only cloud" to "only local" in settings had no effect until the
            # app was restarted. The local provider is resolved per request for
            # the same reason — the server address is editable at runtime.
            runtime_llm_provider = _RoutedLLMProvider(
                ModelRouter(
                    local=_current_local_provider(),
                    cloud=cloud_llm_provider,
                    cloud_model=app.state.settings_service.model().model,
                    runtime_reader=_current_local_config,
                    local_service=app.state.local_model_service,
                )
            )
        app.state.search_service = SearchService(
            FallbackSearchProvider(
                [
                    # 只有两级：厂商自己的内置联网优先，Tavily 兜底（免密钥即可用）。
                    # SearXNG / Bing / DuckDuckGo 那三级抓屏已删除 —— 抓屏是"看着免费、
                    # 维护成本无上限"的典型（上游改版就断，且我们跑不到真实网络里测不到）。
                    ModelSearchProvider(_model_credentials),
                    _LazyTavily(),
                ]
            ),
            WebSearchRunStore(database),
            query_planner=LLMQueryPlanner(runtime_llm_provider),
        )
        app.state.repository_store = repository_store
        app.state.document_store = document_store
        app.state.import_job_store = ImportJobStore(database)
        app.state.vector_cleanup_store = VectorCleanupStore(database)
        app.state.document_mutation_store = DocumentMutationStore(database)
        app.state.vector_store = vector_store
        app.state.document_parser = DocumentParser(formats)
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
        # Persisted settings win over the env default so the settings UI actually
        # takes effect; a fresh install still falls back to AppSettings.
        rag_settings = app.state.settings_service.runtime().rag
        memory_retriever = MemoryRetriever(
            database,
            runtime_embedding_provider,
            vector_store,
            similarity_threshold=rag_settings.memory_recall_min_similarity,
        )
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
                max_sources=rag_settings.max_sources,
            ),
            llm=runtime_llm_provider,
            conversation_store=conversation_store,
            event_broker=InMemoryEventBroker(retention=None),
            message_activity_callback=lambda session_id: summary_service.record_message_activity(
                session_id
            ),
            memory_retriever=memory_retriever,
            search_service=app.state.search_service,
            # 交给模型的工具集合。`WebSearchTool` 复用同一个 SearchService，
            # 所以授权、去重、run 记录与"显式联网"预检索走的是同一套逻辑。
            tool_registry=ToolRegistry([WebSearchTool(app.state.search_service)]),
        )
        app.state.chat_service = chat_service
        app.state.distillation_service = DistillationService(
            conversation_store,
            runtime_llm_provider,
            LocalKnowledgeStore(runtime_settings.data_dir),
            mutation_store=app.state.document_mutation_store,
            indexer=memory_indexer,
            document_saver=lambda **kwargs: save_distillation_document(app, **kwargs),
            event_broker=app.state.distillation_event_broker,
        )
        sync_state_store = RepositorySyncStateStore(database)
        version_store = VersionStore(database)
        graph_store = GraphStore(database)
        rebuild_store = EmbeddingRebuildStore(database)
        refresher = DocumentRefresher(
            document_store=document_store,
            parser=DocumentParser(formats),
            chunker=SemanticChunker(),
            embedding_provider=runtime_embedding_provider,
            vector_store=vector_store,
            documents_dir=runtime_settings.documents_dir,
            version_store=version_store,
            graph_store=graph_store,
        )

        async def read_repo_snapshot(repository_id: str):
            repository = repository_store.get(repository_id)
            if repository is None or not repository.remote_id or not repository.provider:
                return []
            return await read_remote_snapshot(
                registry.get(repository.provider), repository.remote_id
            )

        sync_service = IncrementalSyncService(
            sync_state_store=sync_state_store,
            snapshot_reader=read_repo_snapshot,
            refresher=refresher,
            document_store=document_store,
        )
        sync_scheduler = SyncScheduler(
            sync_service,
            repository_store,
            interval_seconds=runtime_settings.sync_interval_seconds,
            startup_delay_seconds=(
                0.0 if runtime_settings.environment == "test" else SYNC_STARTUP_DELAY_SECONDS
            ),
        )
        app.state.sync_service = sync_service
        app.state.sync_state_store = sync_state_store
        app.state.sync_scheduler = sync_scheduler
        app.state.version_store = version_store
        app.state.graph_store = graph_store
        app.state.embedding_rebuild_store = rebuild_store
        current_dimension = str(runtime_settings.embedding_dimension)
        if SettingStore(database).get("embedding_dimension") != current_dimension:
            for document in document_store.list_all():
                if document.chunk_count > 0:
                    rebuild_store.mark_needs_rebuild(document.id)
            SettingStore(database).set("embedding_dimension", current_dimension)
        app.state.conflict_service = SyncConflictService(
            document_store=document_store,
            sync_state_store=sync_state_store,
            snapshot_reader=read_repo_snapshot,
            registry=registry,
            refresher=refresher,
            repository_store=repository_store,
        )
        summary_task = asyncio.create_task(summary_scheduler.run())
        sync_task = asyncio.create_task(sync_scheduler.run())
        await app.state.import_service.recover_pending_vector_cleanup()
        # 嵌入模型是索引期硬依赖，且模型文件随应用分发（打包脚本缺模型即 exit 1），
        # 所以启动即后台预热，而不是让用户去设置页手动点「加载模型」。
        # ensure_ready() 内部走 asyncio.to_thread，不阻塞事件循环；失败也不致命——
        # 真正用到嵌入的路径（require_ready_embedding）还会再兜底一次并抛 503。
        embedding_warmup_task: asyncio.Task | None = None
        if runtime_settings.environment != "test":
            embedding_warmup_task = asyncio.create_task(runtime_embedding_provider.ensure_ready())
            embedding_warmup_task.add_done_callback(consume_terminal_exception)
            app.state.embedding_prepare_task = embedding_warmup_task
        try:
            yield
        finally:
            if embedding_warmup_task is not None and not embedding_warmup_task.done():
                embedding_warmup_task.cancel()
                await asyncio.gather(embedding_warmup_task, return_exceptions=True)
            tasks = list(app.state.import_tasks)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await chat_service.stop()
            summary_scheduler.stop()
            await summary_task
            sync_scheduler.stop()
            await sync_task
            await registry.close_all()
            database.engine.dispose()

    app = FastAPI(dependencies=[Depends(require_runtime_token)], lifespan=lifespan)
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_bytes=runtime_settings.max_request_body_bytes,
    )
    app.dependency_overrides[get_settings] = lambda: runtime_settings
    app.state.settings = runtime_settings
    app.state.secret_store = runtime_secret_store
    app.state.remote_registry = _injected_registry(providers)
    app.state.embedding_provider = runtime_embedding_provider
    app.state.embedding_prepare_task = None
    app.state.fake_llm_provider = fake_llm_provider
    app.state.import_tasks = set()
    app.state.active_document_mutations = set()
    app.state.document_mutation_registry_lock = Lock()
    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(RequestValidationError, request_validation_handler)
    app.include_router(settings_router)
    app.include_router(embedding_router)
    app.include_router(remote_router)
    app.include_router(plugins_router)
    app.include_router(imports_router)
    app.include_router(import_batches_router)
    app.include_router(repositories_router)
    app.include_router(documents_router)
    app.include_router(sessions_router)
    app.include_router(chat_router)
    app.include_router(search_router)
    app.include_router(web_search_router)
    app.include_router(memory_router)
    app.include_router(local_model_router)
    app.include_router(sync_router)
    app.include_router(graph_router)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", version="0.1.0")

    if runtime_settings.environment == "test":

        @app.get("/_test/domain-error")
        async def test_domain_error() -> None:
            raise DomainError("TEST_CONFLICT", "测试冲突", 409)

    return app
