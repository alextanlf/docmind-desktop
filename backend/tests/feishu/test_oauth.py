from __future__ import annotations

import asyncio
import json
import socket
import urllib.request
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from app.api.errors import DomainError
from app.core.secrets import MemorySecretStore
from app.feishu.oauth import FeishuOAuthFlow, OAuthSettings, build_authorize_url
from app.feishu.provider import FeishuProvider
from app.feishu.tokens import FEISHU_APP_SECRET_REF, FeishuTokenManager
from app.remote.credentials import CredentialStore
from app.storage.database import Database

BASE = "https://open.feishu.cn/open-apis"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _browser_hit(authorize_url: str, *, code: str = "oauth-code", tamper_state: bool = False) -> None:
    """Simulate the browser: follow the authorize URL's loopback redirect."""
    query = parse_qs(urlparse(authorize_url).query)
    redirect_uri = query["redirect_uri"][0]
    state = "tampered" if tamper_state else query["state"][0]
    urllib.request.urlopen(f"{redirect_uri}?code={code}&state={state}", timeout=5).read()


def _flow(port: int, *, tamper_state: bool = False) -> FeishuOAuthFlow:
    def open_url(url: str) -> None:
        loop = asyncio.get_running_loop()
        loop.create_task(asyncio.to_thread(_browser_hit, url, tamper_state=tamper_state))

    return FeishuOAuthFlow(OAuthSettings(port=port, timeout=5.0), open_url=open_url)


@respx.mock
async def test_full_oauth_flow_exchanges_code_for_user_bundle() -> None:
    respx.post(f"{BASE}/auth/v3/app_access_token/internal").mock(
        return_value=httpx.Response(200, json={"code": 0, "app_access_token": "a-1"})
    )
    exchange_route = respx.post(f"{BASE}/authen/v1/oidc/access_token").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "access_token": "u-1",
                    "refresh_token": "ur-1",
                    "expires_in": 7200,
                    "name": "谭凌峰",
                },
            },
        )
    )

    bundle = await _flow(_free_port()).run("cli_a1b2", "s3cret")

    assert bundle.access_token == "u-1"
    assert bundle.refresh_token == "ur-1"
    assert bundle.name == "谭凌峰"
    assert bundle.expires_at is not None and not bundle.expired
    request = exchange_route.calls[0].request
    assert request.headers["Authorization"] == "Bearer a-1"
    assert json.loads(request.content)["code"] == "oauth-code"


@respx.mock
async def test_state_mismatch_fails_the_flow() -> None:
    with pytest.raises(DomainError) as caught:
        await _flow(_free_port(), tamper_state=True).run("cli_a1b2", "s3cret")
    assert caught.value.code == "FEISHU_OAUTH_FAILED"


@respx.mock
async def test_busy_callback_port_maps_to_unavailable() -> None:
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    try:
        with pytest.raises(DomainError) as caught:
            await _flow(port).run("cli_a1b2", "s3cret")
        assert caught.value.code == "FEISHU_OAUTH_UNAVAILABLE"
    finally:
        blocker.close()


def test_authorize_url_carries_client_id_and_redirect() -> None:
    url = build_authorize_url("cli_a1b2", "http://127.0.0.1:9999/cb", "state-1")

    query = parse_qs(urlparse(url).query)
    assert url.startswith("https://accounts.feishu.cn/open-apis/authen/v1/authorize")
    assert query["client_id"] == ["cli_a1b2"]
    assert query["redirect_uri"] == ["http://127.0.0.1:9999/cb"]
    assert query["state"] == ["state-1"]


# -- provider.begin_login wiring ------------------------------------------------


class _StubOAuth:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def run(self, app_id: str, app_secret: str):
        from app.feishu.tokens import UserTokenBundle

        self.calls.append((app_id, app_secret))
        return UserTokenBundle("u-new", "ur-new", None, "谭凌峰")


def _store(database: Database) -> CredentialStore:
    store = CredentialStore(database, MemorySecretStore())
    store.save_secret("feishu", "app", "cli_a1b2:s3cret", FEISHU_APP_SECRET_REF)
    store.mark_state("feishu", "app", "verified", account_label="cli_***b2")
    return store


async def test_begin_login_runs_oauth_and_marks_user_channel(database: Database) -> None:
    store = _store(database)
    oauth = _StubOAuth()
    provider = FeishuProvider(FeishuTokenManager(store), oauth_flow=oauth)  # type: ignore[arg-type]

    result = await provider.begin_login()

    assert result.logged_in is True
    assert result.account_label == "谭***峰"
    assert oauth.calls == [("cli_a1b2", "s3cret")]
    record = store.get("feishu", "user")
    assert record is not None and record.state == "verified"
    assert record.account_label == "谭凌峰"
    assert store.secret_for("feishu", "user") is not None


async def test_begin_login_requires_app_credentials_first(database: Database) -> None:
    store = CredentialStore(database, MemorySecretStore())
    provider = FeishuProvider(FeishuTokenManager(store), oauth_flow=_StubOAuth())  # type: ignore[arg-type]

    with pytest.raises(DomainError) as caught:
        await provider.begin_login()
    assert caught.value.code == "FEISHU_APP_CREDENTIALS_REQUIRED"
