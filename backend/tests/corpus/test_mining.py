"""从已有数据里挖来源：只认站点，不认本地路径。"""
from __future__ import annotations

import pytest

from app.corpus.mining import collect, url_like


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # 网页导入的标识是 `web:https://…`：直接送进 normalize_domain 会因为 scheme 是
        # `web` 而取不到主机名，所以这里必须先把网址抠出来。
        ("web:https://docs.python.org/3/library/asyncio.html", "https://docs.python.org/3/library/asyncio.html"),
        ("https://docs.python.org/3/", "https://docs.python.org/3/"),
        ("docs.python.org", "docs.python.org"),
        ("  https://a.test/x  ", "https://a.test/x"),
    ],
)
def test_site_values_are_recognised(value: str, expected: str) -> None:
    assert url_like(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        # 本地暂存目录不是站点：本机实测 3 篇文档全是这一类，挖出来必须是零。
        "staged://0c5f3a1e-0000-0000-0000-000000000000",
        "staged-collection://collection-1/entry-1",
        "remote://feishu/doc-1",
        "不是网址",
    ],
)
def test_non_site_values_are_ignored(value: object) -> None:
    assert url_like(value) is None


def test_shape_checking_is_left_to_the_derivation_layer() -> None:
    """`url_like` 只负责把文本里的网址抠出来，"它是不是一个公开站点"由
    `normalize_domain` 判断 —— 两层各管一件事，组合起来才是"IP 不会被收录"。

    这条断言把这个分工写下来：哪天有人想在这里加 IP/内网判断，会先看到它。
    """
    from app.corpus.candidates import derive_candidates

    assert url_like("1.2.3.4") == "1.2.3.4"
    assert derive_candidates(document_urls=["1.2.3.4", "http://user:pw@a.test/"]) == []


def test_collect_dedupes_and_keeps_order() -> None:
    values = [
        "staged://abc",
        "web:https://docs.python.org/3/a",
        "https://docs.python.org/3/b",
        "docs.python.org",
        None,
        "https://other.test/",
    ]

    assert collect(values) == [
        "https://docs.python.org/3/a",
        "https://docs.python.org/3/b",
        "docs.python.org",
        "https://other.test/",
    ]


def test_collect_respects_the_limit() -> None:
    values = [f"https://host{index}.test/page" for index in range(20)]

    assert len(collect(values, limit=5)) == 5
