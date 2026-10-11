"""把多个信号合成一份**可审计**的候选来源清单。

设计约束来自两处实测：

1. **冷启动必须不依赖已有数据。** 一个刚装好的 DocMind 没有文档 URL、没有抓取历史、
   没有上游搜索结果（实测全为 0），所以 `seed` / `nomination` / `explicit` 三个信号
   必须能在"什么都没有"时产出候选。
2. **上游是增益层，不是地基。** 本项目最缺的恰恰是"模型不能联网、Tavily 额度打满"
   时的能力，因此把上游结果当候选来源是循环依赖。它在这里的权重最低，且**缺席时
   推导照常工作**（有测试钉住这一点）。

每个候选都带 `evidence`：一句话说明"是谁提名的、凭什么"。用户要能一眼看出某个域名
是**有人核对过的第一方来源**（种子表）还是**模型的一家之言**（提名）—— 两者的可信度
不同，混在一起显示等于把判断责任推给用户却又不给依据。
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit

from app.corpus.seeds import match_topics, seed_domain


class Signal(str, Enum):
    """一个候选是被谁提出来的。取值会被写进证据里，也是排名的输入。"""

    EXPLICIT = "explicit"
    SEED = "seed"
    DOCUMENTS = "documents"
    NOMINATION = "nomination"
    CRAWL_HISTORY = "crawl_history"
    UPSTREAM = "upstream"


#: 排名权重。理由逐条写下来，因为这个顺序就是产品主张：
#:
#: - `EXPLICIT` 最高：用户自己给的，是唯一不需要我们判断的信号。
#: - `SEED` 其次：有人核对过它是第一方来源（有证据、有核对日期）。
#: - `DOCUMENTS` 第三：用户自己的文档引用了它 —— 真实引用比任何猜测都硬。
#: - `NOMINATION` 第四：模型凭参数化知识提名，**未核对**，可能过时或编造。
#: - `CRAWL_HISTORY` 第五：用户试过抓它 —— 说明有意向，但不说明它权威。
#: - `UPSTREAM` 最低：上游排序里出现过的域名。增益层，且极容易被 SEO 站点污染。
_SIGNAL_WEIGHT: dict[Signal, int] = {
    Signal.EXPLICIT: 100,
    Signal.SEED: 40,
    Signal.DOCUMENTS: 30,
    Signal.NOMINATION: 20,
    Signal.CRAWL_HISTORY: 15,
    Signal.UPSTREAM: 5,
}

#: 多个独立信号指向同一个域名时的每个额外加成。
#:
#: 交叉印证确实是证据（种子表里有、文档里也引用过，比只有一条更可信），但加成必须
#: 小于 `EXPLICIT` 与 `SEED` 的差距 —— 否则"很多弱信号"会盖过"用户明确指定"。
_CORROBORATION_BONUS = 4

_MAX_DOMAIN_CHARS = 253
_HOSTNAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")


@dataclass(frozen=True)
class NominatedSource:
    """模型提名的一条来源。`reason` 原样保留 —— 它是给人看的证据，不是给程序看的。"""

    domain: str
    reason: str


@dataclass(frozen=True)
class DomainCandidate:
    domain: str
    signals: tuple[Signal, ...]
    evidence: tuple[str, ...]
    label: str | None = None
    kind: str | None = None
    entry_url: str | None = None
    #: 是否来自**有人核对过**的种子表。模型提名与挖掘出来的都是 False。
    verified: bool = False

    @property
    def score(self) -> int:
        base = max(_SIGNAL_WEIGHT[signal] for signal in self.signals)
        return base + _CORROBORATION_BONUS * (len(self.signals) - 1)

    @property
    def explicit(self) -> bool:
        return Signal.EXPLICIT in self.signals


def normalize_domain(value: str) -> str | None:
    """把一个网址或裸域名规整成小写主机名；不是公开域名时返回 None。

    刻意**不做** eTLD+1 归并（把 `docs.python.org` 压成 `python.org`）：抓取范围与
    礼貌策略都是按主机算的，`docs.python.org` 正是该抓的那个主机；归并需要一个公共
    后缀表，而多带一张表的维护成本高于它带来的去重收益。

    拒绝的东西同样重要：IP、`localhost`、带凭据的 URL、以及任何不像域名的东西。
    它们既不是"公开来源"，也不会被抓取层接受（`safe_http` 的 SSRF 闸门会拒私网），
    放进候选清单只会让用户以为系统发现了什么。
    """
    raw = (value or "").strip().strip("<>\"'").strip()
    if not raw:
        return None
    target = raw if ("//" in raw or raw.startswith("http")) else f"//{raw}"
    parsed = urlsplit(target)
    if parsed.username or parsed.password:
        # 带凭据的 URL 不是"来源"，而且这种写法通常来自复制粘贴的事故。
        return None
    try:
        port = parsed.port
    except ValueError:
        # 端口本身解析不了（例如 `javascript:alert(1)`），那就不是域名。
        return None
    if port is not None:
        # 静默丢掉端口等于改写用户的输入；而公开文档站不会跑在非标准端口上。
        return None
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or len(host) > _MAX_DOMAIN_CHARS or ":" in host:
        # 冒号 = IPv6，不是"来源域名"。
        return None
    host = host.removeprefix("www.")
    if host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
        return None
    if not _HOSTNAME.match(host):
        return None
    if not host.rsplit(".", 1)[1].isalpha():
        # 最后一段是纯数字 = IPv4 或版本号，不是域名。
        return None
    return host


@dataclass
class _Accumulator:
    signals: list[Signal]
    evidence: list[str]
    label: str | None = None
    kind: str | None = None
    entry_url: str | None = None
    verified: bool = False

    def add(
        self,
        signal: Signal,
        evidence: str,
        *,
        label: str | None = None,
        kind: str | None = None,
        entry_url: str | None = None,
        verified: bool = False,
    ) -> None:
        if signal not in self.signals:
            self.signals.append(signal)
        if evidence not in self.evidence:
            self.evidence.append(evidence)
        # 种子表带来的元数据优先：它是被核对过的那一份。
        if self.label is None or (verified and not self.verified):
            self.label = label or self.label
            self.kind = kind or self.kind
            self.entry_url = entry_url or self.entry_url
        self.verified = self.verified or verified


def _mined_counter(urls: Iterable[str]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for value in urls:
        domain = normalize_domain(value)
        if domain is not None:
            counts[domain] += 1
    return counts


def derive_candidates(
    *,
    explicit: Iterable[str] = (),
    text: str | None = None,
    nominated: Iterable[NominatedSource] = (),
    document_urls: Iterable[str] = (),
    crawl_urls: Iterable[str] = (),
    upstream_urls: Iterable[str] = (),
    limit: int = 20,
) -> list[DomainCandidate]:
    """合成候选清单，按可信度排序。

    所有参数都有默认值，所以"什么都不给"是合法调用 —— 那正是**冷启动**的形状：
    只有种子表（若 `text` 命中）与模型提名能产出候选，而上游缺席不影响结果。
    """
    candidates: dict[str, _Accumulator] = {}

    def bucket(domain: str) -> _Accumulator:
        return candidates.setdefault(domain, _Accumulator(signals=[], evidence=[]))

    for value in explicit:
        domain = normalize_domain(value)
        if domain is not None:
            bucket(domain).add(Signal.EXPLICIT, "你指定了这个来源", verified=True)

    if text:
        for topic in match_topics(text):
            for source in topic.sources:
                domain = seed_domain(source.url)
                if not domain:
                    continue
                bucket(domain).add(
                    Signal.SEED,
                    f"种子表（{topic.label}）：{source.evidence}",
                    label=source.label,
                    kind=source.kind,
                    entry_url=source.url,
                    verified=True,
                )

    for nomination in nominated:
        domain = normalize_domain(nomination.domain)
        if domain is not None:
            bucket(domain).add(
                Signal.NOMINATION, f"模型提名（未经核对）：{nomination.reason}"
            )

    mined: tuple[tuple[Signal, Counter[str], str], ...] = (
        (Signal.DOCUMENTS, _mined_counter(document_urls), "你的文档引用了它"),
        (Signal.CRAWL_HISTORY, _mined_counter(crawl_urls), "你之前抓取过它"),
        (Signal.UPSTREAM, _mined_counter(upstream_urls), "它出现在上游搜索结果里"),
    )
    for signal, counts, template in mined:
        for domain, count in counts.items():
            bucket(domain).add(signal, f"{template}（{count} 次）")

    ranked = [
        DomainCandidate(
            domain=domain,
            signals=tuple(acc.signals),
            evidence=tuple(acc.evidence),
            label=acc.label,
            kind=acc.kind,
            entry_url=acc.entry_url,
            verified=acc.verified,
        )
        for domain, acc in candidates.items()
    ]
    # 次级键用域名，保证同分候选的排列稳定 —— 否则同一批输入每次渲染顺序都可能变。
    ranked.sort(key=lambda item: (-item.score, item.domain))
    return ranked[:limit]
