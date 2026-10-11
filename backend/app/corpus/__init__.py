"""第一方网页语料：决定「该抓哪些站点」的部分。

`seeds`     随应用分发的、带证据与核对日期的第一方来源表（冷启动的地基）；
`candidates`把种子 / 模型提名 / 用户指定 / 挖掘信号合成一份可审计的候选清单；
`nomination`让模型凭参数化知识提名来源 —— **不需要联网**，所以它在
             "模型不能联网、Tavily 额度打满"的场景下依然可用。

刻意与 `app.document` 的分工：那边负责"怎么把一个网址抓成可索引的正文"
（有界爬虫、robots、礼貌策略、可达性），这边负责"凭什么决定去抓它"。
"""

from __future__ import annotations

from app.corpus.candidates import (
    DomainCandidate,
    NominatedSource,
    Signal,
    derive_candidates,
    normalize_domain,
)
from app.corpus.nomination import DomainNominator, LLMDomainNominator, parse_nominations
from app.corpus.seeds import (
    SEED_MAX_AGE_DAYS,
    SEED_TOPICS,
    SeedSource,
    SeedTopic,
    match_topics,
    seed_domain,
    seed_review_today,
    stale_seed_topics,
)

__all__ = [
    "SEED_MAX_AGE_DAYS",
    "SEED_TOPICS",
    "DomainCandidate",
    "DomainNominator",
    "LLMDomainNominator",
    "NominatedSource",
    "SeedSource",
    "SeedTopic",
    "Signal",
    "derive_candidates",
    "match_topics",
    "normalize_domain",
    "parse_nominations",
    "seed_domain",
    "seed_review_today",
    "stale_seed_topics",
]
