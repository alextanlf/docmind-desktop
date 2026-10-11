"""来源推导端点：空安装 + 上游全挂时，候选仍然出得来。"""
from __future__ import annotations


def test_seeds_carry_the_result_when_the_model_cannot_nominate(client, auth_headers) -> None:
    """假 services 模式下的聊天模型给不出域名（它只会引用文档片段），于是这里正好
    跑通了最该被保证的那条路径：**提名不可用，候选照常产出**。

    这正是这个功能的前提 —— 它要解决的场景就是"模型不能联网、Tavily 额度打满"，
    所以模型绝不能是必要条件。
    """
    response = client.post(
        "/api/corpus/candidates",
        headers=auth_headers,
        json={"text": "Python 的 asyncio 该怎么用？"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["nomination"] == {"attempted": True, "count": 0}
    domains = [candidate["domain"] for candidate in body["candidates"]]
    assert "docs.python.org" in domains
    # 每条候选都要能说出自己凭什么 —— 没有证据的候选等于让用户猜。
    assert all(candidate["evidence"] for candidate in body["candidates"])
    seeded = next(c for c in body["candidates"] if c["domain"] == "docs.python.org")
    assert seeded["verified"] is True
    assert seeded["entryUrl"] == "https://docs.python.org/3/"
    assert "seed" in seeded["signals"]


def test_explicit_domains_rank_first_and_junk_is_dropped(client, auth_headers) -> None:
    response = client.post(
        "/api/corpus/candidates",
        headers=auth_headers,
        json={
            "text": "python",
            "explicitDomains": ["https://mine.example/docs", "not a domain", "127.0.0.1"],
            "nominate": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["nomination"] == {"attempted": False, "count": 0}
    assert body["candidates"][0]["domain"] == "mine.example"
    assert body["candidates"][0]["verified"] is True
    assert all(candidate["domain"] != "not a domain" for candidate in body["candidates"])


def test_an_empty_request_returns_an_empty_list_instead_of_guessing(client, auth_headers) -> None:
    response = client.post("/api/corpus/candidates", headers=auth_headers, json={})

    assert response.status_code == 200
    assert response.json()["candidates"] == []


def test_upstream_domains_are_accepted_but_never_outrank_a_seed(client, auth_headers) -> None:
    response = client.post(
        "/api/corpus/candidates",
        headers=auth_headers,
        json={
            "text": "python",
            "upstreamUrls": ["https://seo-farm.example/a"],
            "nominate": False,
        },
    )

    domains = [candidate["domain"] for candidate in response.json()["candidates"]]
    assert domains.index("docs.python.org") < domains.index("seo-farm.example")
