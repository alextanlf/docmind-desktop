"""按需抓取：预算、范围、失败原因、以及"整体替换"的语义。"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.corpus.crawl import CorpusCrawler
from app.document.chunker import SemanticChunker
from app.document.parser import DocumentParser
from app.document.politeness import DomainRateLimiter
from app.document.reachability import ReachabilityProbe
from app.document.safe_http import SafeHttpClient
from app.storage.database import Database
from app.storage.repositories import WebCorpusStore


class Resolver:
    async def resolve(self, host: str):
        return ["93.184.216.34"]


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def now(self) -> float:
        return self.value


class Sleeper:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock

    async def __call__(self, seconds: float) -> None:
        self.clock.value += seconds


PAGE = (
    b"<html><head><title>Guide</title></head><body>"
    b"<h1>Guide</h1><p>DocMind guide body with enough words to become a chunk. "
    b"asyncio event loop and tasks are discussed here in some length.</p>"
    b"<a href='/guide/a'>a</a><a href='/outside'>outside</a>"
    b"</body></html>"
)
CHILD = b"# Child\n\nMarkdown child page content, also long enough to chunk."


def routes(**overrides):
    base = {
        "/robots.txt": (200, "text/plain", b"User-agent: *\nAllow: /"),
        "/guide": (200, "text/html", PAGE),
        "/guide/a": (200, "text/markdown", CHILD),
    }
    base.update(overrides)
    return base


def build_crawler(tmp_path: Path, table: dict, *, probe_handler=None, limiter=None):
    def handler(request: httpx.Request) -> httpx.Response:
        outcome = table.get(request.url.path)
        if outcome is None:
            return httpx.Response(404, headers={"content-type": "text/plain"}, content=b"", request=request)
        if isinstance(outcome, Exception):
            raise outcome
        status, media, body = outcome
        return httpx.Response(status, headers={"content-type": media}, content=body, request=request)

    database = Database(f"sqlite+pysqlite:///{tmp_path / 'corpus.sqlite3'}")
    database.upgrade()
    store = WebCorpusStore(database)
    crawler = CorpusCrawler(
        client=SafeHttpClient(resolver=Resolver(), transport=httpx.MockTransport(handler)),
        parser=DocumentParser(),
        chunker=SemanticChunker(),
        store=store,
        probe=(
            ReachabilityProbe(resolver=Resolver(), transport=httpx.MockTransport(probe_handler))
            if probe_handler is not None
            else None
        ),
        limiter=limiter,
        max_bytes=1024 * 1024,
    )
    return crawler, store


@pytest.mark.asyncio
async def test_crawl_stores_pages_and_chunks(tmp_path: Path) -> None:
    crawler, store = build_crawler(tmp_path, routes())

    summary = await crawler.crawl(domain="docs.test", entry_url="https://docs.test/guide")

    assert summary.pages == 2
    assert summary.chunks > 0
    stats = store.stats()["docs.test"]
    assert stats.pages == 2
    assert stats.chunks == summary.chunks
    # 语料要能给出处：每条块的 url 就是引用来源。
    assert {chunk.url for chunk in store.chunks()} == {
        "https://docs.test/guide",
        "https://docs.test/guide/a",
    }


@pytest.mark.asyncio
async def test_recrawl_replaces_the_domain_instead_of_accumulating(tmp_path: Path) -> None:
    """页面被删掉、或改版后内容变了，都要自动反映出来 —— 这是"整体替换"的意义。"""
    crawler, store = build_crawler(tmp_path, routes())
    await crawler.crawl(domain="docs.test", entry_url="https://docs.test/guide")

    crawler, store = build_crawler(
        tmp_path,
        routes(**{"/guide/a": (404, "text/plain", b"")}),
    )
    summary = await crawler.crawl(domain="docs.test", entry_url="https://docs.test/guide")

    assert summary.pages == 1
    assert store.stats()["docs.test"].pages == 1
    assert {chunk.url for chunk in store.chunks()} == {"https://docs.test/guide"}


@pytest.mark.asyncio
async def test_page_budget_stops_the_crawl_and_says_so(tmp_path: Path) -> None:
    """抓完整站不是默认行为：按钮触发的动作必须能结束，而"这不是全部"要说出来。"""
    crawler, _ = build_crawler(tmp_path, routes())

    summary = await crawler.crawl(
        domain="docs.test", entry_url="https://docs.test/guide", max_pages=1
    )

    assert summary.pages == 1
    assert summary.truncated is True


@pytest.mark.asyncio
async def test_scope_keeps_the_crawl_inside_the_entry_path(tmp_path: Path) -> None:
    """入口是 /guide，就不该顺手把整站抓下来。"""
    requested: list[str] = []
    table = routes()

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        status, media, body = table[request.url.path]
        return httpx.Response(status, headers={"content-type": media}, content=body, request=request)

    database = Database(f"sqlite+pysqlite:///{tmp_path / 'scope.sqlite3'}")
    database.upgrade()
    crawler = CorpusCrawler(
        client=SafeHttpClient(resolver=Resolver(), transport=httpx.MockTransport(handler)),
        parser=DocumentParser(),
        chunker=SemanticChunker(),
        store=WebCorpusStore(database),
    )

    await crawler.crawl(domain="docs.test", entry_url="https://docs.test/guide")

    assert "/outside" not in requested


@pytest.mark.asyncio
async def test_robots_denied_pages_are_skipped_with_their_reason(tmp_path: Path) -> None:
    crawler, _ = build_crawler(
        tmp_path,
        routes(**{"/robots.txt": (200, "text/plain", b"User-agent: *\nDisallow: /guide/a")}),
    )

    summary = await crawler.crawl(domain="docs.test", entry_url="https://docs.test/guide")

    assert summary.pages == 1
    assert ("https://docs.test/guide/a", "robots_denied") in summary.skipped


@pytest.mark.asyncio
async def test_a_host_that_cannot_be_reached_reports_the_real_reason(tmp_path: Path) -> None:
    """抓不到时用户要知道是哪一类 —— "抓取失败"四种处置完全不同。"""

    def probe_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    crawler, _ = build_crawler(
        tmp_path,
        routes(**{"/guide": httpx.ConnectTimeout("timed out")}),
        probe_handler=probe_handler,
    )

    summary = await crawler.crawl(domain="docs.test", entry_url="https://docs.test/guide")

    assert summary.pages == 0
    assert summary.dominant_failure() == "unreachable"


@pytest.mark.asyncio
async def test_empty_summary_has_no_failure_reason(tmp_path: Path) -> None:
    """全部成功时不该记一个错误；否则界面上会挂着一个不存在的失败。"""
    crawler, _ = build_crawler(tmp_path, routes())

    summary = await crawler.crawl(domain="docs.test", entry_url="https://docs.test/guide")

    assert summary.dominant_failure() is None


@pytest.mark.asyncio
async def test_politeness_applies_between_pages_of_one_source(tmp_path: Path) -> None:
    """同一来源的相邻两页之间要遵守 robots 声明的间隔 —— 抓语料和抓导入是同一批请求，
    共用同一份礼貌策略。"""
    clock = Clock()
    limiter = DomainRateLimiter(clock=clock, sleep=Sleeper(clock))

    class RecordingLimiter(DomainRateLimiter):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.delays: list[float | None] = []

        async def acquire(self, host, *, crawl_delay=None):  # type: ignore[override]
            self.delays.append(crawl_delay)
            return await super().acquire(host, crawl_delay=crawl_delay)

    recording = RecordingLimiter(clock=clock, sleep=Sleeper(clock))
    crawler, _ = build_crawler(
        tmp_path,
        routes(**{"/robots.txt": (200, "text/plain", b"User-agent: *\nCrawl-delay: 3\nAllow: /")}),
        limiter=recording,
    )

    summary = await crawler.crawl(domain="docs.test", entry_url="https://docs.test/guide")

    assert summary.pages == 2
    # 站点声明的间隔被读到并交给了限流器（真正的等待由限流器负责，见 test_politeness）。
    assert 3.0 in recording.delays
    assert limiter is not None


# --- 增量刷新 ------------------------------------------------------------


def fresh_store(tmp_path: Path, name: str) -> WebCorpusStore:
    database = Database(f"sqlite+pysqlite:///{tmp_path / name}")
    database.upgrade()
    return WebCorpusStore(database)


def crawler_over(store: WebCorpusStore, handler, **kwargs) -> CorpusCrawler:
    return CorpusCrawler(
        client=SafeHttpClient(resolver=Resolver(), transport=httpx.MockTransport(handler)),
        parser=DocumentParser(),
        chunker=SemanticChunker(),
        store=store,
        max_bytes=1024 * 1024,
        **kwargs,
    )


def html(title: str, body: str, links: tuple[str, ...] = ()) -> bytes:
    anchors = "".join(f'<a href="{href}">{href}</a>' for href in links)
    return (
        f"<html><head><title>{title}</title></head><body><h1>{title}</h1>"
        f"<p>{body}</p>{anchors}</body></html>"
    ).encode()



@pytest.mark.asyncio
async def test_unchanged_pages_keep_their_chunks_and_send_validators(tmp_path: Path) -> None:
    """增量刷新的收益就在这里：内容没变的页面不重新切块，块的 id 也不会变。

    块 id 变化本身不影响正确性，但它是一次全量重写 —— 语料越大代价越明显，而且
    任何指向块的引用（将来的引用统计、增量嵌入）都会因此失效。
    """
    store = fresh_store(tmp_path, "incremental.sqlite3")
    body = html("Guide", "asyncio event loop explained at length for chunking")
    requests: list[httpx.Request] = []

    def first(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"User-agent: *\nAllow: /", request=request)
        return httpx.Response(
            200,
            headers={"content-type": "text/html", "etag": '"v1"'},
            content=body,
            request=request,
        )

    crawler = crawler_over(store, first)
    first_summary = await crawler.crawl(domain="docs.test", entry_url="https://docs.test/guide")
    assert first_summary.updated_pages == 1
    chunk_ids = [chunk.id for chunk in store.chunks()]

    def second(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"User-agent: *\nAllow: /", request=request)
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304, request=request)
        return httpx.Response(200, headers={"content-type": "text/html"}, content=body, request=request)

    summary = await crawler_over(store, second).crawl(
        domain="docs.test", entry_url="https://docs.test/guide"
    )

    assert summary.kept_pages == 1
    assert summary.updated_pages == 0
    assert summary.pages == 1
    # 校验器真的带出去了（否则站点永远没机会答 304）。
    assert any(request.headers.get("if-none-match") == '"v1"' for request in requests)
    # 块一个都没被重写。
    assert [chunk.id for chunk in store.chunks()] == chunk_ids



@pytest.mark.asyncio
async def test_200_with_identical_content_is_also_kept(tmp_path: Path) -> None:
    """没有校验器的站点也要照顾：内容哈希一致就不重写。"""
    store = fresh_store(tmp_path, "hash.sqlite3")
    body = html("Guide", "content that will not change between crawls at all")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"User-agent: *\nAllow: /", request=request)
        return httpx.Response(200, headers={"content-type": "text/html"}, content=body, request=request)

    await crawler_over(store, handler).crawl(domain="docs.test", entry_url="https://docs.test/guide")
    chunk_ids = [chunk.id for chunk in store.chunks()]
    summary = await crawler_over(store, handler).crawl(
        domain="docs.test", entry_url="https://docs.test/guide"
    )

    assert summary.kept_pages == 1
    assert summary.updated_pages == 0
    assert [chunk.id for chunk in store.chunks()] == chunk_ids



@pytest.mark.asyncio
async def test_changed_content_replaces_only_that_page(tmp_path: Path) -> None:
    store = fresh_store(tmp_path, "changed.sqlite3")
    bodies = {
        "/guide": html(
            "Guide", "first version of the guide body, long enough", links=("/guide/a",)
        ),
        "/guide/a": html("Child", "child page content, long enough to chunk"),
    }
    crawled: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"User-agent: *\nAllow: /", request=request)
        crawled.append(request.url.path)
        return httpx.Response(200, headers={"content-type": "text/html"}, content=bodies[request.url.path], request=request)

    await crawler_over(store, handler).crawl(domain="docs.test", entry_url="https://docs.test/guide")
    child_ids = [chunk.id for chunk in store.chunks() if chunk.url.endswith("/guide/a")]

    bodies["/guide"] = html(
        "Guide", "second version of the guide body, changed", links=("/guide/a",)
    )
    summary = await crawler_over(store, handler).crawl(
        domain="docs.test", entry_url="https://docs.test/guide"
    )

    assert summary.updated_pages == 1
    assert summary.kept_pages == 1  # 子页没变，块也不该被重写
    # 只有变了的那一页被重写，另一页的块保持原样。
    assert [chunk.id for chunk in store.chunks() if chunk.url.endswith("/guide/a")] == child_ids



@pytest.mark.asyncio
async def test_a_truncated_crawl_never_deletes_pages_it_did_not_reach(tmp_path: Path) -> None:
    """**安全性质**：抓取被预算截断时，没抓到的页面必须留着。

    否则网络慢一点、上限低一点，语料就会凭空缩水 —— 而用户看到的是"抓了一次，
    东西变少了"，且没有任何提示。
    """
    store = fresh_store(tmp_path, "truncated.sqlite3")
    bodies = {
        "/guide": html(
            "Guide", "root page content long enough to chunk", links=("/guide/a",)
        ),
        "/guide/a": html("Child", "child page content long enough to chunk"),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"User-agent: *\nAllow: /", request=request)
        return httpx.Response(200, headers={"content-type": "text/html"}, content=bodies[request.url.path], request=request)

    await crawler_over(store, handler).crawl(domain="docs.test", entry_url="https://docs.test/guide")
    assert store.stats()["docs.test"].pages == 2

    summary = await crawler_over(store, handler).crawl(
        domain="docs.test", entry_url="https://docs.test/guide", max_pages=1
    )

    assert summary.truncated is True
    assert summary.removed_pages == 0
    assert store.stats()["docs.test"].pages == 2



@pytest.mark.asyncio
async def test_a_transient_failure_keeps_the_existing_copy(tmp_path: Path) -> None:
    """超时不是"页面没了"的证据。旧副本留着，下一位用户至少还能读到上一版。"""
    store = fresh_store(tmp_path, "transient.sqlite3")

    def ok(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"User-agent: *\nAllow: /", request=request)
        return httpx.Response(200, headers={"content-type": "text/html"}, content=html("Guide", "content long enough"), request=request)

    await crawler_over(store, ok).crawl(domain="docs.test", entry_url="https://docs.test/guide")

    def flaky(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"User-agent: *\nAllow: /", request=request)
        raise httpx.ReadTimeout("stalled")

    def probe_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("stalled")

    failing = crawler_over(
        store,
        flaky,
        probe=ReachabilityProbe(
            resolver=Resolver(), transport=httpx.MockTransport(probe_handler)
        ),
    )
    summary = await failing.crawl(domain="docs.test", entry_url="https://docs.test/guide")

    assert summary.pages == 1
    assert summary.removed_pages == 0
    # 传输出问题、站点没机会答复状态码，这时探测是唯一的依据。
    assert summary.skipped[0][1] == "throttled"



@pytest.mark.asyncio
async def test_a_404_deletes_the_local_copy_and_says_how_many(tmp_path: Path) -> None:
    store = fresh_store(tmp_path, "gone.sqlite3")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"User-agent: *\nAllow: /", request=request)
        return httpx.Response(200, headers={"content-type": "text/html"}, content=html("Guide", "content long enough"), request=request)

    await crawler_over(store, handler).crawl(domain="docs.test", entry_url="https://docs.test/guide")

    def gone(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"User-agent: *\nAllow: /", request=request)
        return httpx.Response(404, headers={"content-type": "text/plain"}, content=b"", request=request)

    summary = await crawler_over(store, gone).crawl(
        domain="docs.test", entry_url="https://docs.test/guide"
    )

    assert summary.removed_pages == 1
    assert summary.pages == 0
    assert summary.skipped[0][1] == "http_rejected"



@pytest.mark.asyncio
async def test_a_site_rejection_is_classified_without_a_probe(tmp_path: Path) -> None:
    """站点已经亲口说了状态码，就不该再发一次请求去猜 —— 那是多余的网络往来。"""
    store = fresh_store(tmp_path, "no-probe.sqlite3")
    probes: list[str] = []

    def probe_handler(request: httpx.Request) -> httpx.Response:
        probes.append(str(request.url))
        return httpx.Response(200, request=request)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"User-agent: *\nAllow: /", request=request)
        return httpx.Response(403, headers={"content-type": "text/html"}, content=b"", request=request)

    crawler = crawler_over(
        store,
        handler,
        probe=ReachabilityProbe(
            resolver=Resolver(), transport=httpx.MockTransport(probe_handler)
        ),
    )
    summary = await crawler.crawl(domain="docs.test", entry_url="https://docs.test/guide")

    assert summary.skipped[0][1] == "http_rejected"
    assert probes == []
