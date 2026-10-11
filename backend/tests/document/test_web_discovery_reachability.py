"""抓取链路上的可达性与礼貌策略：把「为什么这个 URL 没进来」跑到端到端。

前面的单元测试证明分类与节奏本身是对的；这里证明它们**真的接到了爬虫上** ——
一个注入的探针、一个注入的限流器，不接上去的话前两个模块就只是好看的库。
"""
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from app.document.politeness import DomainRateLimiter
from app.document.reachability import ReachabilityProbe
from app.document.safe_http import SafeHttpClient
from app.document.web_discovery import WebDiscovery
from app.schemas.batches import DiscoveryRequest


class Resolver:
    async def resolve(self, host):
        return ["93.184.216.34"]


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def now(self) -> float:
        return self.value


class Sleeper:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.slept: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.clock.value += seconds


def route_handler(routes: dict):
    """路由表：值为 ``(status, media, body)``，或一个待抛出的异常。"""

    def handler(request: httpx.Request) -> httpx.Response:
        outcome = routes.get(request.url.path)
        if outcome is None:
            return httpx.Response(
                404, headers={"content-type": "text/plain"}, content=b"", request=request
            )
        if isinstance(outcome, Exception):
            raise outcome
        status, media, body = outcome
        return httpx.Response(
            status, headers={"content-type": media}, content=body, request=request
        )

    return handler


async def run_discovery(
    tmp_path: Path,
    routes: dict,
    *,
    probe_handler=None,
    limiter: DomainRateLimiter | None = None,
    use_sitemap: bool = True,
):
    client = SafeHttpClient(
        resolver=Resolver(), transport=httpx.MockTransport(route_handler(routes))
    )
    probe = (
        ReachabilityProbe(
            resolver=Resolver(), transport=httpx.MockTransport(probe_handler)
        )
        if probe_handler is not None
        else None
    )
    request = DiscoveryRequest(
        batch_id=uuid4(),
        source_kind="web",
        repository_id=uuid4(),
        source_descriptor={
            "entryUrl": "https://docs.test/guide",
            "maxDepth": 2,
            "maxPages": 10,
            "useSitemap": use_sitemap,
        },
    )
    events = []

    async def emit(event) -> None:
        events.append(event)

    result = await WebDiscovery(client, tmp_path, probe=probe, limiter=limiter).discover(
        request, emit
    )
    return result, events


def reasons(result) -> list[str]:
    return [entry["reason"] for entry in result.rejected]


@pytest.mark.asyncio
async def test_site_rejection_is_reported_as_rejection_not_as_a_wall(tmp_path: Path) -> None:
    """实测 stackoverflow.com 的形状：403 是站点拒绝，不是网络到不了。

    报错成「不可达」会让用户去找镜像，而正确的动作是换 UA / 换路径 / 改用官方 API。
    """
    result, _ = await run_discovery(
        tmp_path,
        {"/robots.txt": (200, "text/plain", b"User-agent: *\nAllow: /"), "/guide/": (403, "text/html", b"")},
        probe_handler=lambda request: httpx.Response(403, request=request),
        use_sitemap=False,
    )
    assert reasons(result) == ["http_rejected"]


@pytest.mark.asyncio
async def test_throttled_host_is_backed_off_and_then_skipped(tmp_path: Path) -> None:
    """一个主机被限流后，同主机后面的 URL 不是继续敲门，而是记 limit_reached。

    这两件事必须分开：``throttled`` 是站点在限流我们（值得退避后再来），
    ``limit_reached`` 是我们主动放弃（不值得等）。
    """
    clock = Clock()
    limiter = DomainRateLimiter(
        clock=clock,
        sleep=Sleeper(clock),
        min_interval_seconds=0.0,
        max_wait_seconds=1.0,
    )
    routes = {
        "/robots.txt": (200, "text/plain", b"User-agent: *\nAllow: /"),
        "/sitemap.xml": (
            200,
            "application/xml",
            b"<urlset><url><loc>https://docs.test/guide/a</loc></url></urlset>",
        ),
        # 实测 github.com 的形状：连上了，但传输停滞。
        "/guide/": httpx.ReadTimeout("read timed out"),
        "/guide/a": (200, "text/markdown", b"# A\ncontent"),
    }
    result, _ = await run_discovery(
        tmp_path,
        routes,
        probe_handler=lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("stalled")),
        limiter=limiter,
    )
    assert reasons(result) == ["throttled", "limit_reached"]
    # 退避确实建立在该主机上（默认退避起点 5 秒 > 允许等待的 1 秒）。
    assert limiter.strikes("docs.test") == 1


@pytest.mark.asyncio
async def test_robots_crawl_delay_is_honoured_between_pages(tmp_path: Path) -> None:
    """站点声明了 Crawl-delay 就照它来 —— 这是我们能拿到的第一手礼貌依据。"""
    clock = Clock()
    sleeper = Sleeper(clock)
    limiter = DomainRateLimiter(clock=clock, sleep=sleeper)
    routes = {
        "/robots.txt": (200, "text/plain", b"User-agent: *\nCrawl-delay: 3\nAllow: /"),
        "/sitemap.xml": (
            200,
            "application/xml",
            b"<urlset><url><loc>https://docs.test/guide/a</loc></url></urlset>",
        ),
        # 入口 URL 不带尾斜杠（canonicalize_url 不补），所以两条路径都要给。
        "/guide": (200, "text/html", b"<html><title>Root</title><body>root</body></html>"),
        "/guide/": (200, "text/html", b"<html><title>Root</title><body>root</body></html>"),
        "/guide/a": (200, "text/markdown", b"# A\ncontent"),
    }
    result, _ = await run_discovery(tmp_path, routes, limiter=limiter)
    assert result.rejected_count == 0
    assert sleeper.slept == [3.0]


@pytest.mark.asyncio
async def test_without_probe_or_limiter_behaviour_is_unchanged(tmp_path: Path) -> None:
    """两个依赖都是可选的：没传时一次失败仍然只是一次 fetch_failed。

    这是回退保证 —— 生产环境会传，但任何调用方都不该因为没传而崩。
    """
    result, _ = await run_discovery(
        tmp_path,
        {"/robots.txt": (200, "text/plain", b"User-agent: *\nAllow: /"), "/guide/": (500, "text/html", b"")},
        use_sitemap=False,
    )
    assert reasons(result) == ["fetch_failed"]
