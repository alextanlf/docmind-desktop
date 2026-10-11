"""可达性分类的测试。

这些用例全部针对**实测观察到的形状**，不是假想的错误码练习：403/451/传输卡死/
同 CDN 不同主机 —— 每一条都对应一次真实探测的结论。之所以要把它们钉成测试，
是因为最容易写错的一步恰恰是「把一个站点拒绝你，当成网络到不了」。
"""
from dataclasses import FrozenInstanceError
from ipaddress import ip_address

import httpx
import pytest

from app.document.reachability import (
    ProbeResult,
    Reachability,
    ReachabilityProbe,
    classify_error,
    classify_status,
    retry_after_seconds,
)


class Resolver:
    def __init__(self, failing: tuple[str, ...] = ()) -> None:
        self.failing = failing

    async def resolve(self, host: str):
        if host in self.failing:
            raise OSError("nodename nor servname provided, or not known")
        return [ip_address("93.184.216.34")]


class Clock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = value

    def now(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def probe_for(handler, *, clock: Clock | None = None, **kwargs) -> ReachabilityProbe:
    return ReachabilityProbe(
        resolver=Resolver(),
        transport=httpx.MockTransport(handler),
        clock=clock,
        **kwargs,
    )


# --- 纯分类：状态码 -------------------------------------------------------


def test_site_rejection_is_not_unreachable() -> None:
    """实测 stackoverflow.com 403 —— 站点答复了并拒绝，不是墙。"""
    state, retry_after = classify_status(403, {})
    assert state is Reachability.HTTP_REJECTED
    assert retry_after is None


@pytest.mark.parametrize("status", [301, 302, 200, 204])
def test_redirects_and_success_are_reachable(status: int) -> None:
    """实测 docs.python.org 302、codeload.github.com 301 —— 都是可达。"""
    assert classify_status(status, {})[0] is Reachability.OK


def test_legal_restriction_is_its_own_state() -> None:
    """实测 www.infoq.cn 451：重试无用，只能换来源。"""
    state, _ = classify_status(451, {})
    assert state is Reachability.GEO_BLOCKED


@pytest.mark.parametrize("status", [429, 503])
def test_rate_limit_carries_retry_after(status: int) -> None:
    state, retry_after = classify_status(status, {"retry-after": "120"})
    assert state is Reachability.THROTTLED
    assert retry_after == 120


def test_unparsable_retry_after_is_none_rather_than_wrong() -> None:
    """HTTP 日期形式各家格式不一，解析不了就交给上层默认退避。"""
    assert retry_after_seconds({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}) is None
    assert retry_after_seconds({}) is None


# --- 纯分类：传输层异常 ---------------------------------------------------


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (httpx.ConnectTimeout("timed out"), Reachability.UNREACHABLE),
        (httpx.ConnectError("connection refused"), Reachability.UNREACHABLE),
        # 实测 github.com：连上后收到 134 KB 就停住 —— 这是限速，不是不可达。
        (httpx.ReadTimeout("read timed out"), Reachability.THROTTLED),
        (httpx.PoolTimeout("pool exhausted"), Reachability.THROTTLED),
        # 代理坏了和目标到不了是两件事，否则用户会去换镜像。
        (httpx.ProxyError("proxy refused"), Reachability.PROXY_ERROR),
    ],
)
def test_transport_errors_keep_their_meaning(error: httpx.HTTPError, expected: Reachability) -> None:
    assert classify_error(error)[0] is expected


def test_certificate_failure_is_distinct_from_unreachable() -> None:
    state, detail = classify_error(httpx.ConnectError("SSL: CERTIFICATE_VERIFY_FAILED"))
    assert state is Reachability.TLS_ERROR
    assert "证书" in detail


# --- 探测：端到端判定 -----------------------------------------------------


@pytest.mark.asyncio
async def test_probe_separates_site_rejection_from_network_failure() -> None:
    """同一个探针下，403 与 DNS 失败必须给出不同结论。"""
    rejected = probe_for(lambda request: httpx.Response(403))
    result = await rejected.probe("stackoverflow.com")
    assert result.state is Reachability.HTTP_REJECTED
    assert result.http_status == 403
    assert not result.usable

    unreachable = ReachabilityProbe(
        resolver=Resolver(failing=("blocked.test",)),
        transport=httpx.MockTransport(lambda request: httpx.Response(200)),
    )
    result = await unreachable.probe("blocked.test")
    assert result.state is Reachability.UNREACHABLE
    assert result.detail == "DNS 解析失败"
    # 关键是这个：抓取层把解析失败报成「不在允许范围内」，而这里是网络事实。
    assert not result.usable


@pytest.mark.asyncio
async def test_head_unsupported_falls_back_to_range_get() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        if request.method == "HEAD":
            return httpx.Response(405)
        return httpx.Response(200)

    result = await probe_for(handler).probe("no-head.test")
    assert seen == ["HEAD", "GET"]
    assert result.state is Reachability.OK


@pytest.mark.asyncio
async def test_slow_but_reachable_is_flagged_not_failed() -> None:
    clock = Clock()

    def handler(request: httpx.Request) -> httpx.Response:
        clock.advance(7.0)
        return httpx.Response(200)

    result = await probe_for(handler, clock=clock, slow_seconds=5.0).probe("slow.test")
    assert result.state is Reachability.OK
    assert result.slow
    assert result.usable


@pytest.mark.asyncio
async def test_failures_are_cached_longer_than_successes() -> None:
    """失败最贵（一次超时就是 5 秒），所以它的缓存必须比成功更久。"""
    clock = Clock()
    calls = {"n": 0}

    def ok(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200)

    probe = probe_for(ok, clock=clock, ttl_seconds=10.0, failure_ttl_seconds=3600.0)
    await probe.probe("ok.test")
    clock.advance(20.0)
    await probe.probe("ok.test")
    assert calls["n"] == 2  # 成功结论过期，重新探测

    failing = {"n": 0}

    def denied(request: httpx.Request) -> httpx.Response:
        failing["n"] += 1
        return httpx.Response(403)

    probe = probe_for(denied, clock=clock, ttl_seconds=10.0, failure_ttl_seconds=3600.0)
    await probe.probe("denied.test")
    clock.advance(20.0)
    await probe.probe("denied.test")
    assert failing["n"] == 1  # 失败结论仍在缓存里，不再白等一次


@pytest.mark.asyncio
async def test_force_bypasses_the_cache() -> None:
    calls = {"n": 0}

    def denied(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(403)

    probe = probe_for(denied, clock=Clock())
    await probe.probe("denied.test")
    await probe.probe("denied.test", force=True)
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_probe_does_not_confuse_hosts_on_the_same_cdn() -> None:
    """实测：docs.python.org 通、docs.rs 不通，都在 Fastly 上。

    所以缓存的键必须是主机名；这个用例把「按 IP 或 ASN 推断」这个错误钉死。
    """
    clock = Clock()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "docs.rs":
            raise httpx.ConnectTimeout("timed out")
        return httpx.Response(200)

    probe = probe_for(handler, clock=clock)
    assert (await probe.probe("docs.python.org")).state is Reachability.OK
    assert (await probe.probe("docs.rs")).state is Reachability.UNREACHABLE


def test_probe_result_is_immutable_and_self_describing() -> None:
    result = ProbeResult(
        host="example.test",
        state=Reachability.HTTP_REJECTED,
        detail="站点拒绝本次请求（403）",
        checked_at=0.0,
        http_status=403,
    )
    with pytest.raises(FrozenInstanceError):
        result.host = "other.test"  # type: ignore[misc]
    assert not result.usable
