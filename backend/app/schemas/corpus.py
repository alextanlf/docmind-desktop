from __future__ import annotations

from datetime import datetime

from pydantic import Field, model_validator

from app.corpus.candidates import normalize_domain
from app.schemas.batches import CrawlRejectionReason
from app.schemas.common import WireModel


class CorpusCandidateView(WireModel):
    """一条候选来源。`evidence` 是给人看的：谁提名的、凭什么。"""

    domain: str
    signals: list[str]
    evidence: list[str]
    label: str | None = None
    kind: str | None = None
    entry_url: str | None = None
    #: 是否来自有人核对过的种子表。模型提名与挖掘出来的都是 False —— 界面必须能
    #: 把"我们核对过的"与"模型说的"分开显示，否则等于把判断责任推给用户却不给依据。
    verified: bool
    score: int


class CorpusNominationView(WireModel):
    """这次推导里模型提名的结果。分开报，是为了让"模型没给出建议"可见。

    模型不能用、超时、输出解析不了时 `count` 为 0 而候选照常产出 —— 这条降级路径
    必须有办法从响应里看出来，否则用户会以为系统什么都没找。
    """

    attempted: bool
    count: int


class CorpusCandidateRequest(WireModel):
    #: 问题或文档片段。种子表按它匹配话题，模型按它提名。
    text: str | None = Field(default=None, max_length=4000)
    #: 用户自己指定的域名或网址 —— 唯一不需要我们判断的信号。
    explicit_domains: list[str] = Field(default_factory=list, max_length=50)
    #: 已导入文档里出现的网址（离线信号，后期增益）。
    document_urls: list[str] = Field(default_factory=list, max_length=500)
    #: 抓取历史里出现过的网址（离线信号，说明用户有意向）。
    crawl_urls: list[str] = Field(default_factory=list, max_length=500)
    #: 上游搜索结果里的网址。**增益层**：不可用时对结果没有影响，权重也最低。
    upstream_urls: list[str] = Field(default_factory=list, max_length=500)
    nominate: bool = True
    nominate_limit: int = Field(default=5, ge=0, le=10)
    limit: int = Field(default=20, ge=1, le=50)


class CorpusCandidatesResponse(WireModel):
    candidates: list[CorpusCandidateView]
    nomination: CorpusNominationView


class WebSourceInput(WireModel):
    """用户确认收录的一个来源。

    域名在这里就被规整（`normalize_domain`）：写库的、比较的、以后抓取的必须是同一个
    字符串，否则 `WWW.Example.com` 与 `example.com` 会变成两行，而用户看到的是同一个站。
    """

    domain: str
    entry_url: str | None = Field(default=None, max_length=2000)
    label: str | None = Field(default=None, max_length=200)
    kind: str | None = Field(default=None, max_length=32)
    verified: bool = False
    evidence: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _normalize_domain_and_keep_a_pasted_url(self) -> WebSourceInput:
        raw = self.domain.strip()
        normalized = normalize_domain(raw)
        if normalized is None:
            raise ValueError("不是有效的公开域名")
        # 用户把完整网址贴在同一个框里时，"从哪开始抓"就是那个网址。只取域名会把
        # 路径静默丢掉，而路径常常正是他要的那一页（`/docs/guide` 与站点根差别很大）。
        # 存原样：抓取时由 `canonicalize_url` 统一规整。
        if self.entry_url is None and "//" in raw and len(raw) <= 2000:
            self.entry_url = raw
        self.domain = normalized
        return self


class SaveWebSourcesRequest(WireModel):
    sources: list[WebSourceInput] = Field(min_length=1, max_length=50)


class CrawlProgressView(WireModel):
    """进行中的进度。`budget` 一起给出，"已抓 7 页"才有参照。"""

    pages: int = 0
    updated: int = 0
    budget: int = 0


class CrawlOutcomeView(WireModel):
    """上一次抓完的结果。持久展示，而不是一次性的提示 —— 用户下次回来还要看得到。"""

    pages: int = 0
    chunks: int = 0
    kept_pages: int = 0
    updated_pages: int = 0
    removed_pages: int = 0
    skipped_count: int = 0
    truncated: bool = False


class WebSourceView(WireModel):
    domain: str
    entry_url: str | None = None
    label: str | None = None
    kind: str | None = None
    verified: bool
    evidence: list[str]
    #: 语料规模与最近一次抓取。**没有这三个字段，一个一直抓失败的来源看起来和刚
    #: 收录时一模一样** —— 用户没有任何办法知道"到底抓到了没有"。
    page_count: int = 0
    chunk_count: int = 0
    last_crawled_at: datetime | None = None
    #: 上次抓取失败的原因码。刻意用与抓取诊断同一套取值，这样文案只有一份
    #: （在渲染层），后端不需要自己拼一句中文。
    last_error: CrawlRejectionReason | None = None
    #: idle / running / interrupted。进行中的状态落库，所以刷新与重启都不会说谎。
    crawl_state: str = "idle"
    progress: CrawlProgressView | None = None
    last_summary: CrawlOutcomeView | None = None
    added_at: datetime


class WebSourcesResponse(WireModel):
    sources: list[WebSourceView]


class CrawlStartedView(WireModel):
    """起一个后台抓取的结果。

    没有 summary：抓取现在是后台任务，这一次请求只回答"起没起来"。结果要看来源自己
    的 `lastSummary`（它落库，刷新之后还在）。
    """

    started: bool


class CrawlSummaryView(WireModel):
    domain: str
    #: 抓取之后的语料规模（不是本次下载了几页）。
    pages: int
    chunks: int
    #: 内容未变、没有重新切块与重写索引的页数 —— 增量刷新的收益体现在这里。
    kept_pages: int = 0
    updated_pages: int = 0
    removed_pages: int = 0
    skipped_count: int
    #: 因为页数上限提前结束。用户需要知道"这不是全部"。
    truncated: bool


class CrawlSourceResponse(WebSourcesResponse):
    #: 任务是否这一次真的起来了。已经在抓时为 false —— 重复点击不该是错误。
    started: bool
