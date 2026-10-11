"""种子表的纪律：有证据、有核对日期、只收第一方、闸门真的会响。"""
from __future__ import annotations

from datetime import date, timedelta
from typing import get_args

import pytest

from app.corpus.seeds import (
    SEED_MAX_AGE_DAYS,
    SEED_TOPICS,
    SourceKind,
    match_topics,
    seed_domain,
    seed_review_today,
    stale_seed_topics,
)


def test_every_source_is_first_party_and_backed_by_evidence() -> None:
    """说不出理由的域名不该出现在这张表里。"""
    kinds = set(get_args(SourceKind))
    for key, topic in SEED_TOPICS.items():
        assert topic.sources, f"{key} 没有任何来源"
        for source in topic.sources:
            assert source.url.startswith("https://"), f"{key} 的来源不是 https：{source.url}"
            assert source.label.strip(), f"{key} 的 {source.url} 没有显示名"
            assert source.evidence.strip(), f"{key} 的 {source.url} 没有证据"
            assert source.kind in kinds, f"{key} 的 {source.url} 类型非法：{source.kind}"


def test_keywords_are_present_unique_and_not_too_short() -> None:
    """关键词太短会误命中（两三个字母的词到处都是），太随意则等于没有信号。"""
    for key, topic in SEED_TOPICS.items():
        assert topic.keywords, f"{key} 没有关键词"
        folded = [keyword.casefold() for keyword in topic.keywords]
        assert len(folded) == len(set(folded)), f"{key} 有重复关键词"
        assert all(len(keyword) >= 2 for keyword in topic.keywords), f"{key} 有过短的关键词"


def test_review_dates_are_iso_and_not_in_the_future() -> None:
    today = seed_review_today()
    for key, topic in SEED_TOPICS.items():
        try:
            parsed = date.fromisoformat(topic.reviewed_at)
        except ValueError:
            pytest.fail(f"{key} 的核对日期 {topic.reviewed_at!r} 不是 YYYY-MM-DD")
        assert parsed <= today + timedelta(days=1), f"{key} 的核对日期在未来"


def test_no_seed_topic_is_past_its_review_deadline() -> None:
    """闸门本体。文档站会改版、换域名、下掉页面，而"有没有人看过"是唯一能离线检查的。"""
    stale = stale_seed_topics()
    assert not stale, (
        "这些话题的来源表已超过核对周期：\n"
        + "\n".join(f"  - {key}: 最后核对 {reviewed}，已过 {age} 天（上限 {limit} 天）" for key, reviewed, age, limit in stale)
        + "\n\n请逐条打开来源确认域名与文档根路径仍然有效，改完把 reviewed_at 贴到当天。"
    )


def test_the_gate_itself_can_fire() -> None:
    """注入远期日期，闸门必须响 —— 否则它可能只是个永远返回空列表的装饰。"""
    future = seed_review_today() + timedelta(days=SEED_MAX_AGE_DAYS + 1)
    stale = stale_seed_topics(future)
    assert {key for key, *_ in stale} == set(SEED_TOPICS)


def test_seed_domain_uses_the_host_without_www() -> None:
    assert seed_domain("https://www.Postgresql.org/docs/") == "postgresql.org"
    assert seed_domain("https://docs.python.org/3/") == "docs.python.org"


def test_match_topics_hits_chinese_and_english_keywords() -> None:
    assert [topic.key for topic in match_topics("sqlite 的 FTS5 该怎么建索引")] == ["sqlite"]
    assert "python" in [topic.key for topic in match_topics("How do I publish to PyPI?")]


def test_match_topics_ranks_by_hit_count_and_is_stable() -> None:
    first = match_topics("docker kubernetes container compose")
    second = match_topics("docker kubernetes container compose")
    assert first[0].key == "containers"
    assert [topic.key for topic in first] == [topic.key for topic in second]


def test_match_topics_returns_nothing_for_an_unseeded_domain() -> None:
    """种子里没有的领域不要硬凑 —— 那种情况交给模型提名与用户自己贴地址。"""
    assert match_topics("中世纪欧洲的农业产量与轮作制度") == []
    assert match_topics("   ") == []


def test_match_topics_respects_the_limit() -> None:
    assert len(match_topics("python jsx docker", limit=1)) == 1
