from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, HttpUrl, model_validator

from app.schemas.common import WireModel

BatchSourceKind = Literal["staged_directory", "web", "remote_repository", "search_results"]
BatchStateValue = Literal[
    "discovering",
    "awaiting_confirmation",
    "running",
    "paused",
    "completed",
    "completed_with_errors",
    "failed",
    "cancelled",
]
BatchItemDecision = Literal["create", "update", "attach_remote", "skip"]
BatchItemStateValue = Literal["discovered", "queued", "running", "completed", "skipped", "failed", "cancelled"]


class DiscoveryRequest(WireModel):
    batch_id: UUID
    source_kind: BatchSourceKind
    source_descriptor: dict[str, Any]
    repository_id: UUID | None = None


class CachedSourceRef(WireModel):
    # Electron emits UUID-only staged collection item identifiers.
    cache_id: UUID
    media_type: str
    byte_size: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class RemoteBinding(WireModel):
    # No default on purpose: a silently-defaulted provider made new bindings land
    # on Yuque. Every construction site passes the provider explicitly, and a
    # legacy payload without one now fails loudly instead of mis-binding.
    provider: str = Field(min_length=1)
    repository_id: str
    document_id: str
    document_url: str | None = None


class DiscoveredSource(WireModel):
    source_identity: str
    source_revision: str
    title: str
    display_path: str
    media_type: str
    size_bytes: int = Field(ge=0)
    cached_source: CachedSourceRef
    remote_binding: RemoteBinding | None = None


class DiscoveryProgress(WireModel):
    stage: str
    visited: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    message: str


class DiscoveryResult(WireModel):
    sources: list[DiscoveredSource]
    discovery_version: int = Field(ge=1)
    rejected: list[dict[str, Any]] = Field(default_factory=list)
    total_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)


class BatchImportView(WireModel):
    id: UUID
    source_kind: BatchSourceKind
    repository_id: UUID
    state: BatchStateValue
    discovery_version: int = Field(ge=1)
    total_count: int = Field(ge=0)
    selected_count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    progress: int = Field(ge=0, le=100)
    message: str
    error_code: str | None
    error_message: str | None
    retryable: bool
    cancel_requested: bool
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime
    last_event_sequence: int = Field(ge=0)


class BatchItemView(WireModel):
    id: UUID
    batch_id: UUID
    ordinal: int = Field(ge=0)
    title: str
    display_path: str
    media_type: str
    size_bytes: int = Field(ge=0)
    source_revision: str
    allowed_actions: list[BatchItemDecision]
    selected: bool
    decision: BatchItemDecision | None
    state: BatchItemStateValue
    import_job_id: UUID | None
    error_code: str | None
    error_message: str | None
    retryable: bool


class BatchItemPage(WireModel):
    items: list[BatchItemView]
    next_cursor: str | None


class ConfirmBatchItem(WireModel):
    item_id: UUID
    decision: BatchItemDecision


class ConfirmBatchInput(WireModel):
    discovery_version: int = Field(ge=1)
    items: list[ConfirmBatchItem] = Field(max_length=1000)


class RetryBatchInput(WireModel):
    model_config = ConfigDict(extra="forbid")
    item_ids: list[UUID] | None = Field(default=None, max_length=1000, alias="itemIds")


class StagedDirectoryBatchRequest(WireModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["staged_directory"]
    source_id: UUID = Field(alias="sourceId")
    repository_id: UUID = Field(alias="repositoryId")


class WebBatchRequest(WireModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["web"]
    entry_url: HttpUrl = Field(alias="entryUrl")
    repository_id: UUID = Field(alias="repositoryId")
    max_depth: int = Field(default=5, ge=0, le=5, alias="maxDepth")
    max_pages: int = Field(default=200, gt=0, le=200, alias="maxPages")
    use_sitemap: bool = Field(default=True, alias="useSitemap")

    @model_validator(mode="after")
    def no_userinfo(self):
        if self.entry_url.username or self.entry_url.password:
            raise ValueError("网页入口不得包含用户信息")
        return self

#: 一个 URL 为什么没被收录。**这是给用户看的分类，不是内部错误码。**
#:
#: 它必须区分四类处置完全不同的情况：站点答复了并拒绝你（换 UA、换路径，或改用它的
#: 官方 API）、网络层到不了（换镜像或换来源）、法律/地域限制（只能换来源）、以及
#: 我们主动跳过（不值得等）。``fetch_failed`` 是兜底：说不出原因时不含糊其辞，也不猜。
CrawlRejectionReason = Literal[
    "robots_denied",
    "scope_rejected",
    "unsupported_media",
    "limit_reached",
    "unreachable",
    "throttled",
    "http_rejected",
    "geo_blocked",
    "tls_error",
    "proxy_error",
    "fetch_failed",
]


class CrawlDiagnostic(WireModel):
    canonical_url: HttpUrl
    reason: CrawlRejectionReason
    message: str


class BatchDiagnosticView(WireModel):
    """一个被拒的 URL 及其原因。

    ``reason`` 非空：写入时没有记录原因的旧行会由 `error_code` 推导，推导不出来的
    归入 ``fetch_failed`` —— 那与"抓取失败但说不出原因"本来就是同一件事，比留一个
    空字段让界面自己编文案要诚实。
    """

    canonical_url: str
    reason: CrawlRejectionReason


class BatchDiagnosticsPage(WireModel):
    total: int
    #: 列表是否被上限截断 —— 界面必须能说出"还有多少条没列出来"。
    truncated: bool
    items: list[BatchDiagnosticView]


class RemoteRepositoryBatchRequest(WireModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["remote_repository"]
    repository_id: UUID = Field(alias="repositoryId")

WebBatchInput = WebBatchRequest
RemoteRepositoryBatchInput = RemoteRepositoryBatchRequest


class SearchResultsBatchRequest(WireModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["search_results"]
    search_run_id: UUID = Field(alias="searchRunId")
    result_ids: list[UUID] = Field(min_length=1, max_length=10, alias="resultIds")
    repository_id: UUID = Field(alias="repositoryId")


CreateBatchRequest = Annotated[
    StagedDirectoryBatchRequest
    | WebBatchRequest
    | RemoteRepositoryBatchRequest
    | SearchResultsBatchRequest,
    Field(discriminator="kind"),
]
