"""来源推导端点：空安装 + 上游全挂时，候选仍然出得来。"""
from __future__ import annotations

import httpx


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


PAGE = (
    b"<html><head><title>Guide</title></head><body><h1>Guide</h1>"
    b"<p>asyncio event loop and tasks, long enough to become a chunk.</p></body></html>"
)


class _Resolver:
    async def resolve(self, host):
        return ["93.184.216.34"]


def _wait_until_idle(client, auth_headers, domain: str) -> dict:
    """轮询到抓取结束。

    后台任务跑在 ASGI 应用自己的事件循环里（TestClient 的 portal 线程），所以这里用
    同步 sleep 等它就够了 —— 抓完之前界面看到的也是同一份状态。
    """
    import time

    for _ in range(300):
        body = client.get("/api/corpus/sources", headers=auth_headers).json()
        source = next(item for item in body["sources"] if item["domain"] == domain)
        if source["crawlState"] != "running":
            return source
        time.sleep(0.01)
    raise AssertionError("抓取没有在预期时间内结束")


def _swap_crawler(client, table: dict) -> None:
    """把应用里的抓取器换成走 MockTransport 的那个。

    不换的话，一条测试会真的去网络上抓 —— 而这正是"抓取"这个功能最容易在测试里
    悄悄依赖真实网络的地方。
    """
    from app.corpus.crawl import CorpusCrawler
    from app.document.chunker import SemanticChunker
    from app.document.parser import DocumentParser
    from app.document.reachability import ReachabilityProbe
    from app.document.safe_http import SafeHttpClient

    def handler(request: httpx.Request) -> httpx.Response:
        outcome = table.get(request.url.path)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is None:
            return httpx.Response(404, headers={"content-type": "text/plain"}, content=b"", request=request)
        status, media, body = outcome
        return httpx.Response(status, headers={"content-type": media}, content=body, request=request)

    def probe_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    crawler = CorpusCrawler(
        client=SafeHttpClient(resolver=_Resolver(), transport=httpx.MockTransport(handler)),
        parser=DocumentParser(),
        chunker=SemanticChunker(),
        store=client.app.state.web_corpus,
        probe=ReachabilityProbe(resolver=_Resolver(), transport=httpx.MockTransport(probe_handler)),
        max_bytes=1024 * 1024,
    )
    # 两处都要换：服务在启动时按住了那个实例（后台任务用它跑），只换 app.state
    # 不会影响已经建好的服务。
    client.app.state.corpus_crawler = crawler
    client.app.state.corpus_crawl_service.crawler = crawler


def _add_source(client, auth_headers, domain: str, entry: str) -> None:
    response = client.post(
        "/api/corpus/sources",
        headers=auth_headers,
        json={"sources": [{"domain": domain, "entryUrl": entry, "verified": True}]},
    )
    assert response.status_code == 200


def test_crawling_a_source_fills_the_corpus_and_reports_its_size(client, auth_headers) -> None:
    """没有状态字段的话，一个一直抓失败的来源看起来和刚收录时一模一样。"""
    _add_source(client, auth_headers, "docs.test", "https://docs.test/guide")
    _swap_crawler(
        client,
        {
            "/robots.txt": (200, "text/plain", b"User-agent: *\nAllow: /"),
            "/guide": (200, "text/html", PAGE),
        },
    )

    started = client.post("/api/corpus/sources/docs.test/crawl", headers=auth_headers)

    assert started.status_code == 200
    # 后台任务：这一次请求只回答"起没起来"，结果要看来源自己。
    assert started.json()["started"] is True

    source = _wait_until_idle(client, auth_headers, "docs.test")
    assert source["pageCount"] == 1
    assert source["chunkCount"] >= 1
    assert source["lastCrawledAt"] is not None
    assert source["lastError"] is None
    assert source["crawlState"] == "idle"
    assert source["progress"] is None
    # 上次结果落库，刷新之后还在。
    assert source["lastSummary"]["updatedPages"] == 1
    assert source["lastSummary"]["keptPages"] == 0


def test_a_crawl_that_gets_nothing_records_a_reason_code(client, auth_headers) -> None:
    """失败要留下**原因码**（文案在前端只有一份），而不是自己拼一句中文。"""
    _add_source(client, auth_headers, "docs.test", "https://docs.test/guide")
    _swap_crawler(
        client,
        {
            "/robots.txt": (200, "text/plain", b"User-agent: *\nAllow: /"),
            "/guide": httpx.ConnectTimeout("timed out"),
        },
    )

    client.post("/api/corpus/sources/docs.test/crawl", headers=auth_headers)

    source = _wait_until_idle(client, auth_headers, "docs.test")
    assert source["pageCount"] == 0
    # 失败要留下**原因码**（文案在前端只有一份），而不是自己拼一句中文。
    assert source["lastError"] == "unreachable"


def test_crawling_a_source_that_was_never_collected_is_a_404(client, auth_headers) -> None:
    response = client.post("/api/corpus/sources/uncollected.example/crawl", headers=auth_headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CORPUS_SOURCE_UNKNOWN"


def test_cancelling_a_crawl_that_is_not_running_is_not_an_error(client, auth_headers) -> None:
    """界面上的双击、或者任务刚好在这一刻结束 —— 两者都不需要用户做什么。"""
    _add_source(client, auth_headers, "docs.test", "https://docs.test/guide")

    response = client.post("/api/corpus/sources/docs.test/cancel", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["cancelled"] is False
    assert response.json()["sources"][0]["crawlState"] == "idle"


def test_cancelling_an_unknown_source_is_not_an_error_either(client, auth_headers) -> None:
    """和移除一样保持幂等：取消一个不在清单里的域名，回答"没有取消到"就够了。"""
    response = client.post("/api/corpus/sources/never-added.example/cancel", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["cancelled"] is False


def test_removing_a_source_also_clears_its_corpus(client, auth_headers) -> None:
    """只删来源行是不够的：语料留在库里，就既"不属于任何人"又"仍然参与检索" ——
    用户以为删掉了，实际它还在回答他的问题。"""
    _add_source(client, auth_headers, "docs.test", "https://docs.test/guide")
    _swap_crawler(
        client,
        {
            "/robots.txt": (200, "text/plain", b"User-agent: *\nAllow: /"),
            "/guide": (200, "text/html", PAGE),
        },
    )
    client.post("/api/corpus/sources/docs.test/crawl", headers=auth_headers)
    _wait_until_idle(client, auth_headers, "docs.test")
    assert client.app.state.web_corpus.stats().get("docs.test") is not None

    client.delete("/api/corpus/sources/docs.test", headers=auth_headers)

    assert client.app.state.web_corpus.stats().get("docs.test") is None
    assert client.app.state.web_corpus.chunks() == []
    assert client.get("/api/corpus/sources", headers=auth_headers).json()["sources"] == []


def test_removing_a_source_while_it_is_crawling_leaves_no_orphan_corpus(client, auth_headers) -> None:
    """顺序要紧：先停掉抓取再清语料。否则刚清完，后台任务又把语料写回来 ——
    留下一份没有归属、却仍然参与检索的孤儿语料。"""
    import time

    _add_source(client, auth_headers, "docs.test", "https://docs.test/guide")

    gate = __import__("threading").Event()

    class BlockingCrawler:
        async def crawl(self, *, domain, entry_url, max_pages, max_depth=2, on_progress=None):
            import asyncio

            await asyncio.get_running_loop().run_in_executor(None, gate.wait)
            from app.corpus.crawl import CrawlSummary

            return CrawlSummary(
                domain=domain,
                pages=1,
                chunks=1,
                kept_pages=0,
                updated_pages=1,
                removed_pages=0,
                skipped=(),
                truncated=False,
            )

    client.app.state.corpus_crawl_service.crawler = BlockingCrawler()
    client.post("/api/corpus/sources/docs.test/crawl", headers=auth_headers)
    time.sleep(0.05)
    assert _wait_state(client, auth_headers, "docs.test") == "running"

    client.delete("/api/corpus/sources/docs.test", headers=auth_headers)
    gate.set()  # 放开那个阻塞的抓取，让它有机会"写回"语料

    time.sleep(0.05)
    assert client.app.state.web_corpus.stats() == {}
    assert client.app.state.web_corpus.chunks() == []


def _wait_state(client, auth_headers, domain: str) -> str:
    body = client.get("/api/corpus/sources", headers=auth_headers).json()
    return next(item for item in body["sources"] if item["domain"] == domain)["crawlState"]


def test_candidates_are_mined_from_documents_and_crawl_history(client, auth_headers) -> None:
    """用户已有的数据也是信号，而且不需要问任何人。

    挖出来的候选 `verified` 必须是 False：文档引用过某个站，只说明"这份文档提过它"，
    不说明它是一手来源。
    """
    # crawl_entries 有指向批次的强约束，所以先造一个批次（外键不是能绕过的东西）。
    import json as _json

    from app.storage.models import BatchImportRecord, BatchState, CrawlEntryRecord, DocumentRecord

    batch_id = client.app.state.batch_store.create_batch(
        BatchImportRecord(
            source_kind="web",
            source_descriptor_json=_json.dumps({"entryUrl": "https://docs.test/guide"}),
            repository_id=None,
            state=BatchState.AWAITING_CONFIRMATION,
            discovery_version=1,
            message="",
        )
    ).id
    repository = client.app.state.repository_store.create_local(name="挖矿")
    client.app.state.document_store.create(
        DocumentRecord(
            repository_id=repository.id,
            title="asyncio 笔记",
            source_url="web:https://docs.python.org/3/library/asyncio.html",
            source_type="import",
        )
    )
    client.app.state.document_store.create(
        DocumentRecord(
            repository_id=repository.id,
            title="本地文件",
            source_url="staged://0c5f3a1e-0000-0000-0000-000000000000",
            source_type="import",
        )
    )
    client.app.state.crawl_entries.upsert_frontier(
        CrawlEntryRecord(batch_id=batch_id, canonical_url="https://sqlite.org/docs.html", depth=0)
    )

    response = client.post(
        "/api/corpus/candidates",
        headers=auth_headers,
        json={"nominate": False},
    )

    assert response.status_code == 200
    by_domain = {candidate["domain"]: candidate for candidate in response.json()["candidates"]}
    # 网页导入的 `web:https://…` 标识也要能挖出主机名（少抠一层就是个空结果）。
    assert by_domain["docs.python.org"]["signals"] == ["documents"]
    assert by_domain["docs.python.org"]["verified"] is False
    assert "你的文档引用了它" in by_domain["docs.python.org"]["evidence"][0]
    assert by_domain["sqlite.org"]["signals"] == ["crawl_history"]
    # 本地暂存目录不是站点：本机实测 3 篇文档全是这一类，不能被当成域名。
    assert "staged" not in " ".join(by_domain)


def test_topics_are_listed_so_the_first_click_needs_no_typing(client, auth_headers) -> None:
    """首次体验：新用户进来清单是空的，而"写下一个问题"要求他先知道自己想问什么。

    种子表里的话题是随应用分发、逐条带证据的，点一下就能得到确定的结果 —— 还要告诉他
    能得到几个来源（点之前就知道值不值得点）。
    """
    response = client.get("/api/corpus/topics", headers=auth_headers)

    assert response.status_code == 200
    topics = response.json()["topics"]
    keys = {topic["key"] for topic in topics}
    assert {"python", "sqlite", "containers"} <= keys
    assert all(topic["label"] for topic in topics)
    assert all(topic["sourceCount"] >= 1 for topic in topics)

    python = next(topic for topic in topics if topic["key"] == "python")
    assert python["label"] == "Python"
    assert python["sourceCount"] == 2


def test_selecting_a_topic_by_key_gives_its_sources_without_any_text(client, auth_headers) -> None:
    """点选走精确 key，不经过关键词匹配 —— 一键话题因此是确定的，也不需要调模型。"""
    response = client.post(
        "/api/corpus/candidates",
        headers=auth_headers,
        json={"topics": ["sqlite"], "nominate": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["nomination"] == {"attempted": False, "count": 0}
    assert [candidate["domain"] for candidate in body["candidates"]] == ["sqlite.org"]
    assert body["candidates"][0]["verified"] is True
    assert "种子表" in body["candidates"][0]["evidence"][0]
