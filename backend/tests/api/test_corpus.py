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


def test_confirmed_sources_are_stored_and_normalized(client, auth_headers) -> None:
    """写库的、比较的、以后抓取的必须是同一个字符串 —— 否则 `WWW.X.com` 与 `x.com`
    会变成两行，而用户看到的是同一个站。"""
    response = client.post(
        "/api/corpus/sources",
        headers=auth_headers,
        json={
            "sources": [
                {
                    "domain": "HTTPS://WWW.Example.com/docs",
                    "label": "示例文档",
                    "kind": "official_docs",
                    "verified": True,
                    "evidence": ["种子表：示例"],
                }
            ]
        },
    )

    assert response.status_code == 200
    sources = response.json()["sources"]
    assert [source["domain"] for source in sources] == ["example.com"]
    # 贴的是完整网址，它就是抓取入口（否则 /docs 这个路径会被静默丢掉）。
    assert sources[0]["entryUrl"] == "HTTPS://WWW.Example.com/docs"
    assert sources[0]["verified"] is True
    assert sources[0]["evidence"] == ["种子表：示例"]


def test_re_confirming_merges_evidence_and_never_downgrades(client, auth_headers) -> None:
    """同一个站先因种子表被收录、后来又因模型提名被加入 —— 两条理由都该留着。"""
    client.post(
        "/api/corpus/sources",
        headers=auth_headers,
        json={"sources": [{"domain": "docs.python.org", "verified": True, "evidence": ["种子表"]}]},
    )
    response = client.post(
        "/api/corpus/sources",
        headers=auth_headers,
        json={"sources": [{"domain": "docs.python.org", "verified": False, "evidence": ["模型提名"]}]},
    )

    sources = response.json()["sources"]
    assert len(sources) == 1
    assert sources[0]["evidence"] == ["种子表", "模型提名"]
    assert sources[0]["verified"] is True


def test_invalid_domains_are_rejected_loudly(client, auth_headers) -> None:
    """静默丢弃用户输入比报错更糟：他会以为收藏成功了。"""
    response = client.post(
        "/api/corpus/sources",
        headers=auth_headers,
        json={"sources": [{"domain": "127.0.0.1"}]},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_removing_a_source_is_idempotent(client, auth_headers) -> None:
    """双击、或两个窗口同时操作，都会产生一次"删一个已经不在的东西" —— 为此弹错误
    只会让用户以为操作失败了。响应始终是删除后的真实清单。"""
    client.post(
        "/api/corpus/sources",
        headers=auth_headers,
        json={"sources": [{"domain": "docs.python.org"}]},
    )

    first = client.delete("/api/corpus/sources/docs.python.org", headers=auth_headers)
    second = client.delete("/api/corpus/sources/docs.python.org", headers=auth_headers)

    assert first.status_code == 200
    assert first.json()["sources"] == []
    assert second.status_code == 200
    assert second.json()["sources"] == []


def test_confirmed_sources_are_listed_newest_first(client, auth_headers) -> None:
    client.post(
        "/api/corpus/sources",
        headers=auth_headers,
        json={"sources": [{"domain": "b.example"}, {"domain": "a.example"}]},
    )

    sources = client.get("/api/corpus/sources", headers=auth_headers).json()["sources"]

    # 同一批加入共用一个时间戳，所以列表按域名排 —— 否则刚点完"全部收录"，列表看起来
    # 是按用户看不见的构造顺序排列的。
    assert [source["domain"] for source in sources] == ["a.example", "b.example"]
    assert sources[0]["addedAt"] == sources[1]["addedAt"]
