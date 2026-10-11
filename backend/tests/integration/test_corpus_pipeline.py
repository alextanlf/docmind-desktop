"""端到端：从"收录一个来源"到"提问时引用抓到的那一页"。

这条链路此前只有单元测试各自覆盖（爬虫能抓、检索源能搜、链会选它），但没有一条测试
证明**应用真正装配起来的那条链**会把新抓到的正文当成答案来源。装配错误（漏接一级、
把源加在错的位置）恰恰是单元测试看不见的那类问题。

它是这次唯一能替代"亲手点一遍"的东西 —— e2e 在开发机上跑不起来（Electron 起窗即关）。
"""
from __future__ import annotations

from uuid import uuid4

import httpx

from app.document.chunker import SemanticChunker
from app.document.parser import DocumentParser
from app.document.safe_http import SafeHttpClient
from app.schemas.web_search import SearchRequest, SearchRunRequest

PAGE = (
    b"<html><head><title>Quasar guide</title></head><body><h1>Quasar guide</h1>"
    b"<p>quasar neutrino spectroscopy calibration steps for the local corpus pipeline</p>"
    b"</body></html>"
)
QUESTION = "quasar neutrino spectroscopy calibration"


class Resolver:
    async def resolve(self, host: str):
        return ["93.184.216.34"]


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/robots.txt":
        return httpx.Response(
            200, headers={"content-type": "text/plain"}, content=b"User-agent: *\nAllow: /", request=request
        )
    return httpx.Response(
        200, headers={"content-type": "text/html"}, content=PAGE, request=request
    )


def _install_mock_crawler(test_app) -> None:
    """把应用的抓取器换成走 MockTransport 的那个（两处都要换，见下）。"""
    from app.corpus.crawl import CorpusCrawler

    crawler = CorpusCrawler(
        client=SafeHttpClient(resolver=Resolver(), transport=httpx.MockTransport(_handler)),
        parser=DocumentParser(),
        chunker=SemanticChunker(),
        store=test_app.app.state.web_corpus,
        max_bytes=1024 * 1024,
    )
    # 服务在启动时按住了那个实例，只换 app.state 不会影响已经在跑的服务。
    test_app.app.state.corpus_crawler = crawler
    test_app.app.state.corpus_crawl_service.crawler = crawler


async def _crawl_one_source(test_app, domain: str, entry: str) -> None:
    import asyncio

    response = await test_app.client.post(
        "/api/corpus/sources", json={"sources": [{"domain": domain, "entryUrl": entry}]}
    )
    assert response.status_code == 200
    started = await test_app.client.post(f"/api/corpus/sources/{domain}/crawl")
    assert started.json()["started"] is True

    for _ in range(300):
        body = (await test_app.client.get("/api/corpus/sources")).json()
        source = next(item for item in body["sources"] if item["domain"] == domain)
        if source["crawlState"] != "running":
            assert source["pageCount"] == 1, source
            return
        await asyncio.sleep(0.01)
    raise AssertionError("抓取没有在预期时间内结束")


async def test_a_crawled_page_answers_a_question_through_the_real_chain(test_app) -> None:
    """抓到的正文必须能被**应用装配的那条链**当成来源 —— 这才叫"抓下来的东西有用"。"""
    _install_mock_crawler(test_app)
    await _crawl_one_source(test_app, "docs.test", "https://docs.test/guide")

    service = test_app.app.state.search_service
    run = await service.run(
        SearchRunRequest(
            request_id=uuid4(),
            session_id=uuid4(),
            user_message_id=uuid4(),
            query=QUESTION,
        )
    )

    # 由本地语料作答：链上排在它前面的没有别人，排在后面的是需要 key 或会打满额度的上游。
    assert run.provider == "web_corpus", run.provider
    assert [str(result.canonical_url) for result in run.results] == ["https://docs.test/guide"]
    # 引用要能指向真正抓下来的正文，而不是一个空壳。
    assert "quasar neutrino spectroscopy" in run.results[0].content


async def test_the_corpus_is_available_to_the_chain_only_after_a_crawl(test_app) -> None:
    """空语料不该出现在链上：一个永远返回空的来源只会让每次搜索白扫一遍全库。

    这条同时是把"链里真的有本地语料这一级"钉住 —— 只断言搜索能出结果的话，某天有人
    把这级从链上摘掉、而结果恰好由上游给出，测试仍然会绿。
    """
    chain = test_app.app.state.search_service.provider
    corpus = next(item for item in chain.providers if item.name == "web_corpus")
    # 没有这个标记，"显式联网时本地降级"的规则永远不会生效 —— 规则对了而标记没打，
    # 表现和没写规则一模一样。
    assert corpus.local is True
    assert corpus.available() is False

    _install_mock_crawler(test_app)
    await _crawl_one_source(test_app, "docs.test", "https://docs.test/guide")

    assert corpus.available() is True

    # 显式联网时链把本地来源挪到最后：用户点「联网搜索」该先拿到网上的东西。
    explicit = chain.ordered(SearchRequest(query="x", authorization_mode="explicit"))
    assert explicit[-1].name == "web_corpus"
    # 默认档位下仍然本地优先。
    assert chain.ordered(SearchRequest(query="x"))[0].name == "web_corpus"
