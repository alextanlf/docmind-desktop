"""域名级礼貌策略：每主机最小间隔、指数退避与 ``Retry-After``。

为什么需要它：抓取层今天对「被限流」没有任何反应 —— 429 / ``Retry-After`` 只在 LLM
与 Tavily 的客户端里处理过，爬虫侧一次都没有。对同一个域名连续敲门只会更快被封，
而站点已经明确答复 451/403 之后继续重试，对自己和对方都没有好处。

三条规则，按优先级：

1. ``Retry-After`` 是站点给出的具体指令，照做 —— 它优先于我们算出来的退避；
2. ``robots.txt`` 的 ``Crawl-delay`` 是该站点的期望间隔，作为该主机的间隔下限；
3. 两者都没有时用默认间隔，并在连续被限流时指数退避（有上限）。

以及一条硬边界：**等待有上限**。需要等的时间超过 ``max_wait_seconds`` 就不等 ——
``acquire`` 返回 False，由调用方记成「跳过这个 URL」，而不是让一次用户提问卡在某个
站点的退避里。这也是 ``limit_reached``（一个早就存在却始终没有产生者的枚举值）
终于有了确定的含义：不是「站点拒绝」，而是「我们主动放弃，因为不值得等」。

依赖注入（clock / sleep），所以全部等待逻辑可以在测试里跑完 —— 不需要真的睡 5 分钟。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Protocol

#: 没有任何声明的站点，两次请求之间的默认间隔。宁可慢，也不要制造「这个客户端在
#: 扫我」的印象 —— 被抓取的站点没有义务区分善意爬虫和扫描器。
DEFAULT_MIN_INTERVAL_SECONDS = 1.0

#: 单次请求最多愿意等多久。超过就跳过（记 ``limit_reached``）。
DEFAULT_MAX_WAIT_SECONDS = 30.0

#: 退避起点与上限。连续被限流时按 2 的幂增长。
DEFAULT_BACKOFF_BASE_SECONDS = 5.0
DEFAULT_MAX_BACKOFF_SECONDS = 300.0


class Clock(Protocol):
    def now(self) -> float: ...


class Sleeper(Protocol):
    async def __call__(self, seconds: float) -> None: ...


class DomainRateLimiter:
    """每个主机一份节奏：最小间隔 + 退避窗口 + 连续被限流的计数。"""

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS,
        max_wait_seconds: float = DEFAULT_MAX_WAIT_SECONDS,
        backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS,
        max_backoff_seconds: float = DEFAULT_MAX_BACKOFF_SECONDS,
    ) -> None:
        self.clock = clock
        self.sleep = sleep or asyncio.sleep
        self.min_interval_seconds = min_interval_seconds
        self.max_wait_seconds = max_wait_seconds
        self.backoff_base_seconds = backoff_base_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self._last_started: dict[str, float] = {}
        self._not_before: dict[str, float] = {}
        self._strikes: dict[str, int] = {}

    def interval_for(self, host: str, *, crawl_delay: float | None = None) -> float:
        """该主机的请求间隔：站点声明的 ``Crawl-delay`` 与默认值取大。

        取大而不是取声明的值：``Crawl-delay: 0`` 很常见，照字面执行等于没有礼貌策略。
        """
        declared = crawl_delay if crawl_delay is not None else 0.0
        return max(self.min_interval_seconds, declared)

    async def acquire(self, host: str, *, crawl_delay: float | None = None) -> bool:
        """等到可以请求这个主机为止。

        返回 False 表示「等太久了，放弃」—— 调用方应记 ``limit_reached`` 并跳过，
        而不是重试。注意：即使返回 False 也不会更新节奏，所以别的主机不受影响。
        """
        ready_at = max(
            self._ready_by_interval(host, crawl_delay),
            self._not_before.get(host, 0.0),
        )
        wait = ready_at - self._now()
        if wait > self.max_wait_seconds:
            return False
        if wait > 0:
            await self.sleep(wait)
        self._last_started[host] = self._now()
        return True

    def penalize(self, host: str, *, retry_after_seconds: int | None = None) -> float:
        """记录一次限流，返回该主机的退避秒数。

        ``Retry-After`` 优先：站点说了等多久就等多久，不要用我们的退避去覆盖它 ——
        猜得比它短会继续被拒，猜得比它长是白白浪费。没有它才按连续次数指数退避。
        """
        strikes = self._strikes.get(host, 0) + 1
        self._strikes[host] = strikes
        if retry_after_seconds is not None:
            backoff = float(max(0, retry_after_seconds))
        else:
            backoff = min(
                self.backoff_base_seconds * (2 ** (strikes - 1)),
                self.max_backoff_seconds,
            )
        self._not_before[host] = self._now() + backoff
        return backoff

    def succeeded(self, host: str) -> None:
        """一次成功抓取，清掉退避与连续计数。

        间隔（``_last_started``）故意保留：成功也要保持节奏，否则一个响应快的站点
        会被我们用最高频率打满。
        """
        self._strikes.pop(host, None)
        self._not_before.pop(host, None)

    def strikes(self, host: str) -> int:
        return self._strikes.get(host, 0)

    def _ready_by_interval(self, host: str, crawl_delay: float | None) -> float:
        last = self._last_started.get(host)
        if last is None:
            return 0.0
        return last + self.interval_for(host, crawl_delay=crawl_delay)

    def _now(self) -> float:
        return self.clock.now() if self.clock is not None else time.monotonic()
