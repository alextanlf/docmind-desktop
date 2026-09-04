from __future__ import annotations

import ipaddress
import time
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from app.api.errors import DomainError


class Resolver(Protocol):
    async def resolve(self, host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]: ...


class Clock(Protocol):
    def now(self) -> float: ...


@dataclass(frozen=True)
class HttpSnapshot:
    url: str
    status_code: int
    media_type: str
    headers: dict[str, str]
    body: bytes


def canonicalize_url(value: str) -> str:
    p = urlsplit(value)
    if p.scheme.lower() not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise DomainError("CRAWL_SCOPE_REJECTED", "网页地址不在允许范围内", 400)
    host = p.hostname.lower()
    port = p.port
    netloc = host
    if ":" in host:
        netloc = f"[{host}]"
    if port and port != (443 if p.scheme.lower() == "https" else 80):
        netloc += f":{port}"
    path = p.path or "/"
    if path != "/" and not path.endswith("/"):
        path = path.rstrip("/") or "/"
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(p.query, keep_blank_values=True)
            if not (k.lower().startswith("utm_") or k.lower() in {"gclid", "fbclid"})
        ],
        doseq=True,
    )
    return urlunsplit((p.scheme.lower(), netloc, path, query, ""))


def path_prefix_for_entry(url: str) -> str:
    path = urlsplit(canonicalize_url(url)).path
    if path == "/":
        return "/"
    # canonicalize_url normalizes entry paths to a directory boundary. Keeping
    # that boundary prevents /docs from accidentally including /docs-other.
    return path if path.endswith("/") else f"{path}/"


def in_scope(url: str, entry_origin: str, prefix: str) -> bool:
    p, origin = urlsplit(canonicalize_url(url)), urlsplit(canonicalize_url(entry_origin))
    return (p.scheme, p.netloc) == (origin.scheme, origin.netloc) and (
        p.path == prefix.rstrip("/") or p.path.startswith(prefix)
    )


class SafeHttpClient:
    def __init__(
        self,
        *,
        resolver: Resolver,
        transport: httpx.AsyncBaseTransport,
        clock: Clock | None = None,
        user_agent: str = "DocMind/2",
    ) -> None:
        self.resolver, self.transport, self.clock = resolver, transport, clock
        self.user_agent = user_agent
        self._robots: dict[str, tuple[float, str]] = {}

    async def _check_host(self, url: str) -> None:
        host = urlsplit(url).hostname
        if not host:
            raise DomainError("CRAWL_SCOPE_REJECTED", "网页地址不在允许范围内", 400)
        try:
            addresses = await self.resolver.resolve(host)
        except (OSError, ValueError):
            raise DomainError("CRAWL_SCOPE_REJECTED", "网页地址解析失败", 400) from None
        if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise DomainError("CRAWL_SCOPE_REJECTED", "网页地址不在允许范围内", 400)

    async def get(self, url: str, *, max_bytes: int, redirect_limit: int = 5,
                  scope_origin: str | None = None, scope_prefix: str | None = None) -> HttpSnapshot:
        current = canonicalize_url(url)
        for hops in range(redirect_limit + 1):
            await self._check_host(current)
            try:
                async with httpx.AsyncClient(
                    transport=self.transport, follow_redirects=False
                ) as client:
                    response = await client.get(current)
            except httpx.HTTPError:
                raise DomainError("CRAWL_FETCH_FAILED", "网页抓取失败", 502, True) from None
            if response.status_code in {301, 302, 303, 307, 308}:
                if hops >= redirect_limit or not response.headers.get("location"):
                    raise DomainError("CRAWL_SCOPE_REJECTED", "重定向次数超过限制", 400)
                nxt = canonicalize_url(urljoin(current, response.headers["location"]))
                if scope_origin is not None and scope_prefix is not None and not in_scope(nxt, scope_origin, scope_prefix):
                    raise DomainError("CRAWL_SCOPE_REJECTED", "重定向超出抓取范围", 400)
                current = nxt
                continue
            if response.status_code >= 400:
                raise DomainError(
                    "CRAWL_FETCH_FAILED", "网页抓取失败", 502, response.status_code >= 500
                )
            body = response.content
            if len(body) > max_bytes:
                raise DomainError("DOCUMENT_TOO_LARGE", "文档超过大小限制", 413)
            return HttpSnapshot(
                current,
                response.status_code,
                response.headers.get("content-type", "").split(";", 1)[0].lower(),
                dict(response.headers),
                body,
            )
        raise DomainError("CRAWL_SCOPE_REJECTED", "重定向次数超过限制", 400)

    async def robots_allowed(self, url: str, user_agent: str, *, scope_origin: str | None = None,
                             scope_prefix: str | None = None) -> bool:
        if scope_origin is not None and scope_prefix is not None and not in_scope(url, scope_origin, scope_prefix):
            return False
        p = urlsplit(canonicalize_url(url))
        origin = f"{p.scheme}://{p.netloc}"
        now = self.clock.now() if self.clock else time.monotonic()
        cached = self._robots.get(origin)
        if cached and cached[0] > now:
            body = cached[1]
        else:
            try:
                # robots.txt is fetched at the origin root; constrain redirects
                # to that origin while allowing the root robots path.
                snap = await self.get(origin + "/robots.txt", max_bytes=128 * 1024,
                                       scope_origin=origin, scope_prefix="/")
                body = snap.body.decode("utf-8", "replace")
            except DomainError:
                return False
            self._robots[origin] = (now + 300, body)
        path = p.path or "/"
        blocked = False
        active = False
        for line in body.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, val = [x.strip() for x in line.split(":", 1)]
            if key.lower() == "user-agent":
                active = val == "*" or val.lower() == user_agent.lower()
            elif key.lower() == "disallow" and active and val and path.startswith(val):
                blocked = True
        return not blocked
