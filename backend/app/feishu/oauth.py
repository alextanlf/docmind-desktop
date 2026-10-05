"""Feishu OAuth user-authorization flow over a local loopback redirect.

The desktop app cannot receive a remote redirect, so ``begin_login`` spins
a one-shot HTTP server on ``127.0.0.1`` (fixed port, configurable via
``DOCMIND_FEISHU_OAUTH_PORT``) and opens the system browser at the Feishu
authorize page. The user must register
``http://127.0.0.1:<port>/api/oauth/feishu/callback`` in the app's
「安全设置 → 重定向 URL」 list.

The network/browser touchpoints are constructor-injected so tests can run
the full exchange against a fake code supplier.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse

from app.api.errors import DomainError
from app.feishu import client
from app.feishu.tokens import UserTokenBundle, _expiry_from

DEFAULT_OAUTH_PORT = 17389
OAUTH_CALLBACK_PATH = "/api/oauth/feishu/callback"
_DEFAULT_TIMEOUT_SECONDS = 180.0

_SUCCESS_HTML = (
    "<html><head><meta charset='utf-8'><title>DocMind</title></head>"
    "<body style='font-family:sans-serif;text-align:center;margin-top:4em'>"
    "<h2>飞书授权完成</h2><p>可以关闭此页面，返回 DocMind。</p>"
    "</body></html>"
)
_ERROR_HTML = (
    "<html><head><meta charset='utf-8'><title>DocMind</title></head>"
    "<body style='font-family:sans-serif;text-align:center;margin-top:4em'>"
    "<h2>飞书授权失败</h2><p>请返回 DocMind 重试。</p>"
    "</body></html>"
)


@dataclass(frozen=True)
class OAuthSettings:
    port: int = DEFAULT_OAUTH_PORT
    timeout: float = _DEFAULT_TIMEOUT_SECONDS

    @staticmethod
    def from_env() -> OAuthSettings:
        try:
            port = int(os.getenv("DOCMIND_FEISHU_OAUTH_PORT", DEFAULT_OAUTH_PORT))
        except ValueError:
            port = DEFAULT_OAUTH_PORT
        return OAuthSettings(port=port)

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.port}{OAUTH_CALLBACK_PATH}"


def build_authorize_url(app_id: str, redirect_uri: str, state: str) -> str:
    query = urlencode({"client_id": app_id, "redirect_uri": redirect_uri, "state": state})
    return f"{client.FEISHU_AUTHORIZE_URL}?{query}"


class FeishuOAuthFlow:
    """Runs the authorize → loopback-callback → token-exchange sequence."""

    def __init__(
        self,
        settings: OAuthSettings | None = None,
        open_url: Callable[[str], None] | None = None,
    ) -> None:
        self._settings = settings or OAuthSettings.from_env()
        self._open_url = open_url or (lambda url: webbrowser.open(url))

    async def run(self, app_id: str, app_secret: str) -> UserTokenBundle:
        state = secrets.token_urlsafe(16)
        code = await self._await_code(app_id, state)
        app_token = await client.fetch_app_access_token(app_id, app_secret)
        data = await client.exchange_user_access_token(app_token, code)
        access_token = str(data["access_token"])
        name = data.get("name") or None
        if not name:
            try:
                name = await client.fetch_user_name(access_token)
            except DomainError:
                name = None
        return UserTokenBundle(
            access_token=access_token,
            refresh_token=data.get("refresh_token") or None,
            expires_at=_expiry_from(data),
            name=name,
        )

    # -- loopback server -------------------------------------------------------

    async def _await_code(self, app_id: str, expected_state: str) -> str:
        loop = asyncio.get_running_loop()
        result: asyncio.Future[str] = loop.create_future()

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                request_line = await asyncio.wait_for(reader.readline(), timeout=10)
                target = request_line.decode("latin-1").split(" ")[1]
                query = parse_qs(urlparse(target).query)
                code = (query.get("code") or [None])[0]
                state = (query.get("state") or [None])[0]
                if code and state == expected_state:
                    if not result.done():
                        result.set_result(code)
                    body = _SUCCESS_HTML
                else:
                    if not result.done():
                        result.set_exception(
                            DomainError(
                                "FEISHU_OAUTH_FAILED",
                                "飞书授权回调校验失败，请重试",
                                401,
                                False,
                                "重新点击授权",
                            )
                        )
                    body = _ERROR_HTML
                await self._respond(writer, body)
            except Exception:  # noqa: BLE001 - malformed requests are ignored
                try:
                    await self._respond(writer, _ERROR_HTML)
                except Exception:  # noqa: BLE001,S110 - the one-shot server is
                    # tearing down anyway; nothing left to report the failure to
                    pass
            finally:
                writer.close()

        try:
            server = await asyncio.start_server(handle, "127.0.0.1", self._settings.port)
        except OSError as error:
            raise DomainError(
                "FEISHU_OAUTH_UNAVAILABLE",
                f"无法启动本地授权回调服务（端口 {self._settings.port} 被占用）",
                503,
                True,
                "关闭占用该端口的程序后重试",
            ) from error

        url = build_authorize_url(
            app_id, self._settings.redirect_uri, expected_state
        )
        async with server:
            try:
                self._open_url(url)
            except Exception as error:
                raise DomainError(
                    "FEISHU_OAUTH_UNAVAILABLE",
                    "无法打开系统浏览器完成飞书授权",
                    503,
                    True,
                    f"手动访问授权链接：{url}",
                ) from error
            try:
                return await asyncio.wait_for(result, timeout=self._settings.timeout)
            except TimeoutError as error:
                raise DomainError(
                    "FEISHU_OAUTH_TIMEOUT",
                    "飞书授权超时，未完成登录",
                    408,
                    True,
                    "重新点击授权",
                ) from error

    @staticmethod
    async def _respond(writer: asyncio.StreamWriter, body: str) -> None:
        payload = body.encode("utf-8")
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
            + f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode("latin-1")
            + payload
        )
        await writer.drain()
