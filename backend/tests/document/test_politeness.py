"""域名级礼貌策略的测试。

全部等待通过假时钟 + 假 sleeper 完成，所以没有一个用例真的睡觉 —— 否则
「退避封顶」和「等太久就放弃」这两条最该被验证的规则会因为太慢而没人写。
"""
import pytest

from app.document.politeness import DomainRateLimiter


class Clock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def now(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class Sleeper:
    """真的睡就等于真的等，所以这里把时间推进假时钟。"""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.slept: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.clock.value += seconds


def limiter(clock: Clock, sleeper: Sleeper, **kwargs) -> DomainRateLimiter:
    return DomainRateLimiter(clock=clock, sleep=sleeper, **kwargs)


@pytest.mark.asyncio
async def test_first_request_to_a_host_does_not_wait() -> None:
    clock = Clock()
    sleeper = Sleeper(clock)
    assert await limiter(clock, sleeper).acquire("docs.test") is True
    assert sleeper.slept == []


@pytest.mark.asyncio
async def test_second_request_waits_out_the_interval() -> None:
    clock = Clock()
    sleeper = Sleeper(clock)
    rate = limiter(clock, sleeper, min_interval_seconds=1.0)
    await rate.acquire("docs.test")
    await rate.acquire("docs.test")
    assert sleeper.slept == [1.0]


@pytest.mark.asyncio
async def test_hosts_do_not_share_a_rhythm() -> None:
    clock = Clock()
    sleeper = Sleeper(clock)
    rate = limiter(clock, sleeper)
    await rate.acquire("a.test")
    await rate.acquire("b.test")
    assert sleeper.slept == []


@pytest.mark.asyncio
async def test_robots_crawl_delay_raises_the_floor_but_never_lowers_it() -> None:
    """``Crawl-delay: 0`` 很常见；照字面执行等于没有礼貌策略。"""
    clock = Clock()
    sleeper = Sleeper(clock)
    rate = limiter(clock, sleeper, min_interval_seconds=1.0)
    assert rate.interval_for("slow.test", crawl_delay=3.0) == 3.0
    assert rate.interval_for("fast.test", crawl_delay=0.0) == 1.0
    assert rate.interval_for("silent.test", crawl_delay=None) == 1.0

    await rate.acquire("slow.test", crawl_delay=3.0)
    await rate.acquire("slow.test", crawl_delay=3.0)
    assert sleeper.slept == [3.0]


@pytest.mark.asyncio
async def test_waiting_too_long_is_skipped_rather_than_absorbed() -> None:
    """一次用户提问不能卡在某个站点的退避里 —— 返回 False 让调用方记 limit_reached。"""
    clock = Clock()
    sleeper = Sleeper(clock)
    rate = limiter(clock, sleeper, max_wait_seconds=5.0)
    rate.penalize("busy.test", retry_after_seconds=600)
    assert await rate.acquire("busy.test") is False
    assert sleeper.slept == []


@pytest.mark.asyncio
async def test_backoff_grows_exponentially_and_caps() -> None:
    clock = Clock()
    rate = limiter(
        clock,
        sleeper := Sleeper(clock),
        backoff_base_seconds=5.0,
        max_backoff_seconds=60.0,
    )
    assert rate.penalize("busy.test") == 5.0
    assert rate.penalize("busy.test") == 10.0
    assert rate.penalize("busy.test") == 20.0
    assert rate.penalize("busy.test") == 40.0
    assert rate.penalize("busy.test") == 60.0  # 封顶
    assert rate.penalize("busy.test") == 60.0
    assert sleeper.slept == []


@pytest.mark.asyncio
async def test_retry_after_wins_over_our_own_guess() -> None:
    """站点说了等多久就等多久，不拿我们的退避去覆盖它。"""
    clock = Clock()
    rate = limiter(clock, Sleeper(clock), backoff_base_seconds=300.0)
    assert rate.penalize("busy.test", retry_after_seconds=2) == 2.0
    assert rate.penalize("busy.test", retry_after_seconds=2) == 2.0


@pytest.mark.asyncio
async def test_acquire_waits_exactly_for_the_remaining_backoff() -> None:
    clock = Clock()
    sleeper = Sleeper(clock)
    rate = limiter(clock, sleeper, min_interval_seconds=1.0, max_wait_seconds=30.0)
    rate.penalize("busy.test", retry_after_seconds=10)
    clock.advance(4.0)
    assert await rate.acquire("busy.test") is True
    assert sleeper.slept == [6.0]


@pytest.mark.asyncio
async def test_success_clears_backoff_but_keeps_the_interval() -> None:
    clock = Clock()
    sleeper = Sleeper(clock)
    rate = limiter(clock, sleeper, min_interval_seconds=1.0)
    rate.penalize("flaky.test")
    rate.penalize("flaky.test")
    assert rate.strikes("flaky.test") == 2

    rate.succeeded("flaky.test")
    assert rate.strikes("flaky.test") == 0
    # 退避窗口没了：这次不必等 10 秒，立刻能请求。
    clock.advance(0.1)
    assert await rate.acquire("flaky.test") is True
    assert sleeper.slept == []
    # 但节奏保留：成功之后第二次请求仍要等满间隔，否则响应快的站点会被最高频率打满。
    await rate.acquire("flaky.test")
    assert sleeper.slept == [1.0]
