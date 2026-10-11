"""候选推导：可审计、可复现，且**不依赖上游**。"""
from __future__ import annotations

import pytest

from app.corpus.candidates import (
    NominatedSource,
    Signal,
    derive_candidates,
    normalize_domain,
)


def test_cold_start_without_upstream_still_produces_candidates() -> None:
    """本模块存在的理由：一个刚装好的 DocMind 什么都没有 —— 没有文档 URL、没有抓取
    历史、上游也搜不了（实测三者全为 0）。这时推导仍必须给出候选，否则"不能联网时
    该抓什么"这件事永远没有答案。
    """
    candidates = derive_candidates(text="Python 的 asyncio 该怎么用？")

    assert candidates
    top = candidates[0]
    assert top.domain == "docs.python.org"
    assert top.verified is True
    assert Signal.SEED in top.signals
    # 种子带来的抓取入口与显示名也要跟着走，否则上层还得再查一次表。
    assert top.entry_url == "https://docs.python.org/3/"
    assert top.label == "Python 官方文档"
    assert any("种子表" in item for item in top.evidence)


def test_nothing_at_all_yields_nothing() -> None:
    """没有信号就不该凭空造候选 —— 空清单是一个诚实的答案。"""
    assert derive_candidates() == []


def test_model_nomination_alone_is_enough_but_is_marked_unverified() -> None:
    candidates = derive_candidates(
        text="某公司内部的部署流程",
        nominated=[NominatedSource("docs.example-corp.com", "该公司的开发者文档")],
    )

    assert [candidate.domain for candidate in candidates] == ["docs.example-corp.com"]
    assert candidates[0].verified is False
    # 证据必须写明它是模型说的，而不是我们核对过的 —— 系统不该把猜测显示成事实。
    assert any("未经核对" in item for item in candidates[0].evidence)
    assert any("该公司的开发者文档" in item for item in candidates[0].evidence)


def test_signals_corroborate_but_stay_one_candidate() -> None:
    only_seed = next(
        c for c in derive_candidates(text="python") if c.domain == "docs.python.org"
    )
    both = derive_candidates(
        text="python", nominated=[NominatedSource("docs.python.org", "Python 官方文档")]
    )
    merged = next(c for c in both if c.domain == "docs.python.org")

    assert sum(1 for c in both if c.domain == "docs.python.org") == 1
    assert {Signal.SEED, Signal.NOMINATION} <= set(merged.signals)
    assert len(merged.evidence) == 2
    # 交叉印证提高可信度，但（见下一条）永远盖不过用户显式指定。
    assert merged.score > only_seed.score


def test_many_weak_signals_never_outrank_an_explicit_choice() -> None:
    candidates = derive_candidates(
        explicit=["https://mine.example"],
        document_urls=["https://other.example/a"] * 3,
        crawl_urls=["https://other.example/b"] * 3,
        upstream_urls=["https://other.example/c"] * 3,
        nominated=[NominatedSource("other.example", "模型推荐")],
    )

    assert candidates[0].domain == "mine.example"
    assert candidates[0].explicit is True
    assert candidates[0].verified is True
    assert any("你指定" in item for item in candidates[0].evidence)


def test_upstream_is_the_weakest_signal() -> None:
    """上游是增益层。它排在被核对过的种子之后，而且缺席时推导照常工作。"""
    candidates = derive_candidates(
        text="python", upstream_urls=["https://seo-farm.example/a"]
    )
    domains = [candidate.domain for candidate in candidates]

    assert domains.index("docs.python.org") < domains.index("seo-farm.example")
    upstream = next(c for c in candidates if c.domain == "seo-farm.example")
    assert upstream.signals == (Signal.UPSTREAM,)
    assert upstream.verified is False


def test_mined_signals_carry_their_counts_as_evidence() -> None:
    candidates = derive_candidates(
        document_urls=[
            "https://docs.python.org/3/a",
            "https://docs.python.org/3/b",
            "https://docs.python.org/3/c",
        ]
    )

    assert candidates[0].domain == "docs.python.org"
    assert any("你的文档引用了它（3 次）" in item for item in candidates[0].evidence)


def test_limit_caps_the_list_without_rescoring() -> None:
    urls = [f"https://docs{i}.example/page" for i in range(10)]
    limited = derive_candidates(document_urls=urls, limit=3)

    assert len(limited) == 3
    assert limited == derive_candidates(document_urls=urls)[:3]


@pytest.mark.parametrize(
    "junk",
    [
        "",
        "   ",
        "not a domain",
        "localhost",
        "127.0.0.1",
        "10.0.0.1",
        "192.168.1.1",
        "http://user:pw@example.com/",
        "example",
        "1.2.3",
        "[::1]",
        "foo.local",
        "docs.test:8080",
        "javascript:alert(1)",
    ],
)
def test_junk_input_is_rejected(junk: str) -> None:
    """IP、内网名、带凭据的 URL 都不是"公开来源"；抓取层也不会接受它们。"""
    assert normalize_domain(junk) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("HTTPS://WWW.Example.COM/Path", "example.com"),
        ("docs.python.org", "docs.python.org"),
        ("  example.com.  ", "example.com"),
        ("https://example.com", "example.com"),
        ("https://example.com/very/deep/path?x=1#frag", "example.com"),
    ],
)
def test_domains_are_normalized(value: str, expected: str) -> None:
    assert normalize_domain(value) == expected


# --- 点选话题：精确选取，不靠关键词碰运气 --------------------------------


def test_an_explicit_topic_gives_exactly_its_sources() -> None:
    """界面上一键选「Python」时用户已经表明了他要什么，不该再让他依赖关键词匹配。"""
    candidates = derive_candidates(topics=["python"])

    assert [candidate.domain for candidate in candidates] == ["docs.python.org", "pypi.org"]
    assert all(candidate.verified for candidate in candidates)
    assert all(candidate.entry_url for candidate in candidates)


def test_an_unknown_topic_is_ignored_rather_than_guessed() -> None:
    """话题 key 来自接口，不认识的只可能来自旧版本或被改过的请求 —— 不猜，给空。"""
    assert derive_candidates(topics=["no_such_topic"]) == []


def test_topic_selection_and_text_matching_compose_without_duplicates() -> None:
    """点了一个话题、又写了一句命中同一话题的问题时，来源不能出现两遍。"""
    candidates = derive_candidates(topics=["python"], text="Python 的 asyncio 怎么用")

    assert [candidate.domain for candidate in candidates] == ["docs.python.org", "pypi.org"]
    assert len(candidates[0].evidence) == 1


def test_several_topics_are_all_included_and_the_order_is_stable() -> None:
    """多选话题时全部来源都要在，而且**每次调用的顺序一致**。

    顺序由可信度决定（同分按域名），不由点选顺序决定 —— 这是刻意的：用户看到的第一条
    永远是最可信的那条，而不是"他最后点的那个话题"。这里锁的是稳定性，因为排列每次
    不同会让人以为列表在乱动。
    """
    first = derive_candidates(topics=["sqlite", "python"])
    second = derive_candidates(topics=["python", "sqlite"])

    assert {candidate.domain for candidate in first} == {
        "sqlite.org",
        "docs.python.org",
        "pypi.org",
    }
    assert [candidate.domain for candidate in first] == [
        candidate.domain for candidate in second
    ]
