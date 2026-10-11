"""域名可达性：把「抓不到」拆成可据以决策的原因。

抓取层目前的错误分类只有两种：``CRAWL_FETCH_FAILED``（DNS 失败、连接超时、TLS 错误、
被拒绝的 4xx/5xx 全塌进同一个 502）与 ``CRAWL_SCOPE_REJECTED``（连「DNS 解析失败」也
归到这里，而且不可重试）。于是「被墙」「站点拒绝了你」「你自己并发打太高被上游限速」
在系统里是同一种东西 —— 而这三者的处置完全不同：前两者该换路径或换来源，后者该退避。

本机实测（2026-10）是这张状态表的依据，不是推测：

- ``stackoverflow.com`` → **403**（换浏览器 UA 仍 403），而 ``api.stackexchange.com`` → 200。
  403 是「站点答复了并拒绝你」，不是网络不可达；同一份内容的官方 API 反而通畅。
- ``docs.python.org``（Fastly 151.101.0.223）可达，``docs.rs``（Fastly 151.101.129.91）
  连接超时 —— **同一个 CDN 上不同主机结论相反**，所以缓存的键必须是主机名，
  不能按 IP 或 ASN 推断。
- ``www.infoq.cn`` → **451**：地域/法律限制，重试无用，只能换来源。
- ``github.com`` 连上后在传输中卡死（收到 134 KB 后超时），``api.github.com`` → 200/0.47s。
  「通，但被限速」是独立状态，和「不可达」不能混。
- ``crates.io`` → 403，``docs.python.org`` → 302：**HTTP 层的拒绝/重定向都不代表到不了**。

边界（刻意不做的事）：

- 不做策略判断。「不可达该不该换镜像、换替代来源、降频还是放弃」是调用方的事；
  这里只负责**如实观察并分类**。
- 不做 SSRF 闸门。拒绝私网目标仍是 ``safe_http`` 的职责，而且那里的拒绝有安全含义，
  必须和这里的「到不了」分开表达 —— 这也是本模块存在的理由之一。
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, cast, get_args

import httpx

from app.schemas.batches import CrawlRejectionReason

#: 成功结果的缓存时长。可达性会变（站点挂了又活），所以不能永久缓存。
DEFAULT_TTL_SECONDS = 6 * 3600.0

#: 失败结果的缓存时长。
#:
#: 比成功结果**更长**是有意的：失败是最贵的一种结果 —— 一次连接超时默认就要
#: 等满 5 秒，而它最可能发生在「每次提问都要问一遍同一个域名」的路径上。
#: 缓存太短，用户每次提问都要白等一轮超时；缓存到 24 小时，又可能一整天都不去
#: 重试一个其实已经恢复的站点。折中值放在这里，调用方可以覆盖。
DEFAULT_FAILURE_TTL_SECONDS = 24 * 3600.0

#: 超过它就算 slow。网页抓取到了几秒这个量级，「提问时等它」已经没有意义了。
DEFAULT_SLOW_SECONDS = 5.0

DEFAULT_TIMEOUT_SECONDS = 5.0

#: HEAD 不被支持时（一些站点回 405/501）改用 GET + Range 重试。
_HEAD_UNSUPPORTED = frozenset({405, 501})

#: 判定 TLS/证书问题的关键词。httpx 把证书与握手失败统一包成 ``ConnectError``，
#: 只能从消息里区分 —— 而「证书错了」和「连不上」该给用户的提示完全不同。
_TLS_MARKERS = ("ssl", "certificate", "tls", "handshake")


class Resolver(Protocol):
    async def resolve(self, host: str) -> list[object]: ...


class Clock(Protocol):
    def now(self) -> float: ...


class Reachability(str, Enum):
    """一次探测的结论。只描述观察到的事实，不含「该怎么办」。"""

    #: 站点答复了，且状态码 < 400（含 3xx）。
    OK = "ok"
    #: 站点答复了但拒绝这次请求：403 / 404 / 410 …
    #: 意思是「换 UA、换路径、或改用它的官方 API」，不是网络问题。
    HTTP_REJECTED = "http_rejected"
    #: 451：地域或法律限制。重试无用，只能换来源。
    GEO_BLOCKED = "geo_blocked"
    #: 429 / 503 带 Retry-After，或连接建立后传输停滞 —— 退避后可能成功。
    THROTTLED = "throttled"
    #: DNS 解析失败 / 连接超时 / 连接被拒 —— 网络层到不了。
    UNREACHABLE = "unreachable"
    #: 证书或握手失败。与 UNREACHABLE 分开：前者可能只是证书过期或时钟不对。
    TLS_ERROR = "tls_error"
    #: 代理本身不可用。与 UNREACHABLE 分开：否则用户会以为是站点挂了，
    #: 然后去换镜像 —— 而真正坏掉的是他配的代理。
    PROXY_ERROR = "proxy_error"


@dataclass(frozen=True)
class ProbeResult:
    host: str
    state: Reachability
    detail: str
    checked_at: float
    http_status: int | None = None
    latency_ms: int | None = None
    retry_after_seconds: int | None = None
    #: 能答复，但慢到不适合在提问路径上等。
    slow: bool = False

    @property
    def usable(self) -> bool:
        """这次探测是否拿到了可用内容。

        只有 ``OK``。``HTTP_REJECTED`` 刻意不算：站点明确拒绝了这次请求，
        把它当"可用"会让上层把一个 403 页面当成正文索引进去。
        """
        return self.state is Reachability.OK


def retry_after_seconds(headers: Mapping[str, str]) -> int | None:
    """解析 ``Retry-After``，只认秒数形式。

    HTTP 日期形式（``Retry-After: Wed, 21 Oct 2026 07:28:00 GMT``）需要解析日期，
    而各家实现的日期格式并不统一；解析失败时返回 ``None``，由上层用默认退避，
    比猜一个错误的具体秒数安全。
    """
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        seconds = int(str(raw).strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def classify_status(status: int, headers: Mapping[str, str]) -> tuple[Reachability, int | None]:
    """把 HTTP 状态码映射成状态（不含慢速判定）。"""
    if status == 451:
        return Reachability.GEO_BLOCKED, None
    if status in (429, 503):
        return Reachability.THROTTLED, retry_after_seconds(headers)
    if status >= 400:
        return Reachability.HTTP_REJECTED, None
    return Reachability.OK, None


def classify_error(error: httpx.HTTPError) -> tuple[Reachability, str]:
    """把传输层异常映射成状态。

    顺序要紧：``ConnectTimeout`` 与 ``ConnectError`` 在 httpx 里是同级的兄弟，
    而证书失败被包在 ``ConnectError`` 里 —— 先判超时，再从消息里认证书。
    """
    if isinstance(error, httpx.ProxyError):
        return Reachability.PROXY_ERROR, "代理不可用"
    if isinstance(error, httpx.ConnectTimeout):
        return Reachability.UNREACHABLE, "连接超时"
    if isinstance(error, (httpx.ReadTimeout, httpx.WriteTimeout)):
        # 连上了但传输停滞 —— 实测里 github.com 就是这个形状。
        return Reachability.THROTTLED, "连接建立后传输停滞"
    if isinstance(error, httpx.PoolTimeout):
        # 本地并发上限，不是远端的问题。分开说，免得被当成站点故障。
        return Reachability.THROTTLED, "本地连接池耗尽"
    if isinstance(error, httpx.ConnectError):
        message = str(error).lower()
        if any(marker in message for marker in _TLS_MARKERS):
            return Reachability.TLS_ERROR, "证书或握手失败"
        return Reachability.UNREACHABLE, "连接失败"
    return Reachability.UNREACHABLE, "网络请求失败"


class ReachabilityProbe:
    """按主机探测可达性，并缓存结论。

    依赖全部注入（resolver / transport / clock），所以整条判定路径可以在测试里
    用假对象跑完，不需要真网络 —— 这也是唯一能在 CI 里验证「403 不等于墙」的办法。
    """

    def __init__(
        self,
        *,
        resolver: Resolver,
        transport: httpx.AsyncBaseTransport,
        clock: Clock | None = None,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        failure_ttl_seconds: float = DEFAULT_FAILURE_TTL_SECONDS,
        slow_seconds: float = DEFAULT_SLOW_SECONDS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.resolver = resolver
        self.transport = transport
        self.clock = clock
        self.ttl_seconds = ttl_seconds
        self.failure_ttl_seconds = failure_ttl_seconds
        self.slow_seconds = slow_seconds
        self.timeout_seconds = timeout_seconds
        self._cache: dict[str, ProbeResult] = {}

    def cached(self, host: str) -> ProbeResult | None:
        """取未过期的缓存结论，不做新请求。"""
        result = self._cache.get(host)
        if result is None:
            return None
        if self._now() - result.checked_at >= self._ttl_for(result):
            return None
        return result

    async def probe(self, host: str, *, force: bool = False) -> ProbeResult:
        if not force:
            cached = self.cached(host)
            if cached is not None:
                return cached
        result = await self._probe_once(host)
        self._cache[host] = result
        return result

    async def _probe_once(self, host: str) -> ProbeResult:
        started = self._now()
        try:
            await self.resolver.resolve(host)
        except (OSError, ValueError):
            # 注意与 safe_http 的区别：那里把解析失败报成 CRAWL_SCOPE_REJECTED
            # （安全闸门），这里报成 UNREACHABLE（网络事实）。被墙的域名首先
            # 就死在这一步，用户该看到的是"解析不了"，不是"不在允许范围内"。
            return self._result(host, Reachability.UNREACHABLE, "DNS 解析失败", started)

        async with httpx.AsyncClient(
            transport=self.transport, follow_redirects=False, timeout=self.timeout_seconds
        ) as client:
            outcome = await self._request(client, host)
            if isinstance(outcome, ProbeResult):
                return outcome
            status, headers = outcome

            state, retry_after = classify_status(status, headers)
            latency_ms = int((self._now() - started) * 1000)
            return self._result(
                host,
                state,
                self._describe(state, status),
                started,
                http_status=status,
                latency_ms=latency_ms,
                retry_after_seconds=retry_after,
                slow=self._now() - started >= self.slow_seconds,
            )

    async def _request(self, client: httpx.AsyncClient, host: str) -> tuple[int, Mapping[str, str]] | ProbeResult:
        """发一次探测请求；传输层失败直接变成 ProbeResult 返回。"""
        url = f"https://{host}/"
        try:
            response = await client.head(url)
        except httpx.HTTPError as error:
            state, detail = classify_error(error)
            return self._result(host, state, detail, self._now())
        if response.status_code in _HEAD_UNSUPPORTED:
            try:
                response = await client.get(url, headers={"range": "bytes=0-0"})
            except httpx.HTTPError as error:
                state, detail = classify_error(error)
                return self._result(host, state, detail, self._now())
        return response.status_code, response.headers

    @staticmethod
    def _describe(state: Reachability, status: int) -> str:
        if state is Reachability.OK:
            return "可达" if status < 300 else f"可达（重定向 {status}）"
        if state is Reachability.GEO_BLOCKED:
            return "地域或法律限制（451）"
        if state is Reachability.THROTTLED:
            return f"被限流（{status}）"
        return f"站点拒绝本次请求（{status}）"

    def _result(
        self,
        host: str,
        state: Reachability,
        detail: str,
        started: float,
        *,
        http_status: int | None = None,
        latency_ms: int | None = None,
        retry_after_seconds: int | None = None,
        slow: bool = False,
    ) -> ProbeResult:
        return ProbeResult(
            host=host,
            state=state,
            detail=detail,
            checked_at=self._now(),
            http_status=http_status,
            latency_ms=latency_ms,
            retry_after_seconds=retry_after_seconds,
            slow=slow,
        )

    def _ttl_for(self, result: ProbeResult) -> float:
        return self.ttl_seconds if result.state is Reachability.OK else self.failure_ttl_seconds

    def _now(self) -> float:
        if self.clock is None:
            return time.monotonic()
        return self.clock.now()


#: 可达性状态 → 给用户看的拒绝原因。
#:
#: 这张表放在 `Reachability` 之后：它是模块级常量，键要引用枚举成员，写在类定义
#: 之前会在导入时直接 NameError。
_REASON_BY_STATE: dict[Reachability, CrawlRejectionReason] = {
    Reachability.UNREACHABLE: "unreachable",
    Reachability.THROTTLED: "throttled",
    Reachability.HTTP_REJECTED: "http_rejected",
    Reachability.GEO_BLOCKED: "geo_blocked",
    Reachability.TLS_ERROR: "tls_error",
    Reachability.PROXY_ERROR: "proxy_error",
}

#: 上游状态码 → 拒绝原因。**状态码是确定的观察**，优先于"再探一次"。
_REASON_BY_UPSTREAM_STATUS: dict[int, CrawlRejectionReason] = {
    451: "geo_blocked",
    429: "throttled",
    503: "throttled",
}


def reason_for_status(status: int) -> CrawlRejectionReason:
    """从上游状态码得出原因。

    这是**首选**的判定依据：状态码是站点亲口说的话，而探测是我们在旁边猜。有了它
    就不必为了分类再发一次请求 —— 后面那条探测只在传输层失败（DNS、超时、TLS）时
    才需要，因为那时确实没有状态码可看。
    """
    mapped = _REASON_BY_UPSTREAM_STATUS.get(status)
    if mapped is not None:
        return mapped
    return "http_rejected" if status >= 400 else "fetch_failed"


#: 与可达性无关、但同样会造成「这个 URL 没进来」的内部错误码。
#:
#: 两套拼写都在表里：`CRAWL_*` 是 `safe_http` 抛出的，裸名是爬虫写进
#: `crawl_entries.error_code` 的。少一套，历史行就会退化成"抓取失败"。
_REASON_BY_ERROR_CODE: dict[str, CrawlRejectionReason] = {
    "CRAWL_SCOPE_REJECTED": "scope_rejected",
    "SCOPE_REJECTED": "scope_rejected",
    "ROBOTS_DENIED": "robots_denied",
    "UNSUPPORTED_MEDIA": "unsupported_media",
    "CRAWL_RATE_LIMITED": "limit_reached",
    # 超限今天被报成 fetch_failed（"网页抓取失败"），而它是**我们**的限制，
    # 不是站点的问题 —— 用户该看到的是"太大，跳过"，而不是"抓取失败"。
    "DOCUMENT_TOO_LARGE": "limit_reached",
}


def rejection_reason(
    error_code: str | None,
    probe: ProbeResult | None = None,
    upstream_status: int | None = None,
) -> CrawlRejectionReason:
    """把一个抓取失败的内部错误码，翻译成给用户看的原因分类。

    优先级：**确定的错误码**（范围被拒、超过大小上限）→ **上游状态码** → 探测结论。
    状态码排在探测之前：它是站点亲口说的话，而探测是我们在旁边猜；而且有状态码时
    根本不需要再发一次请求。

    探测结论为 ``OK`` 或根本没有探测结论时返回 ``fetch_failed``：**宁可说不出原因，
    也不猜。** 一个"其实站点好好的、只是这次抽风"的失败被标成"不可达"，会让用户
    去换镜像，而真正该做的是重试。
    """
    if error_code is not None and error_code in _REASON_BY_ERROR_CODE:
        return _REASON_BY_ERROR_CODE[error_code]
    if upstream_status is not None:
        return reason_for_status(upstream_status)
    if probe is not None:
        mapped = _REASON_BY_STATE.get(probe.state)
        if mapped is not None:
            return mapped
    return "fetch_failed"


#: 契约里声明的全部取值。读取历史数据时用它判断一个已落库的字符串是否仍然认识。
DECLARED_REJECTION_REASONS: frozenset[str] = frozenset(get_args(CrawlRejectionReason))


def stored_reason(value: str | None, error_code: str | None = None) -> CrawlRejectionReason:
    """读取已落库的拒绝原因：认识就用，不认识就退回按错误码推导。

    "不认识"是真实存在的情况：库比代码新（降级安装）或某个取值将来被删掉时，表里
    会留下这一版读不懂的字符串。**让它 500 不是选项** —— 诊断信息读得粗糙，也比
    整页打不开好；而按错误码推导给出的仍然是一个可据以行动的类别。
    """
    if value is not None and value in DECLARED_REJECTION_REASONS:
        return cast(CrawlRejectionReason, value)
    return rejection_reason(error_code)
