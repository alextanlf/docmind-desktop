from __future__ import annotations

from datetime import datetime

from pydantic import Field, model_validator

from app.corpus.candidates import normalize_domain
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


class WebSourceView(WireModel):
    domain: str
    entry_url: str | None = None
    label: str | None = None
    kind: str | None = None
    verified: bool
    evidence: list[str]
    added_at: datetime


class WebSourcesResponse(WireModel):
    sources: list[WebSourceView]
