from __future__ import annotations

from pydantic import Field

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
