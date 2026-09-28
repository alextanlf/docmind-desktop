from __future__ import annotations

import json
import time

import httpx
import pytest
import respx

from app.api.errors import DomainError
from app.core.secrets import MemorySecretStore
from app.feishu.tokens import (
    FEISHU_APP_SECRET_REF,
    FEISHU_USER_SECRET_REF,
    FeishuTokenManager,
    UserTokenBundle,
    parse_app_credentials,
)
from app.remote.credentials import CredentialStore
from app.storage.database import Database


@pytest.fixture
def secret_store() -> MemorySecretStore:
    return MemorySecretStore()


@pytest.fixture
def store(database: Database, secret_store: MemorySecretStore) -> CredentialStore:
    return CredentialStore(database, secret_store)


def _seed_app(store: CredentialStore, state: str = "verified") -> None:
    store.save_secret("feishu", "app", "cli_a1b2:s3cret", FEISHU_APP_SECRET_REF)
    store.mark_state("feishu", "app", state, account_label="cli_***b2")


def _seed_user(store: CredentialStore, *, expired: bool = False) -> None:
    bundle = UserTokenBundle(
        access_token="u-old",
        refresh_token="ur-old",
        expires_at=time.time() - 60 if expired else time.time() + 3600,
        name="谭凌峰",
    )
    store.save_secret("feishu", "user", bundle.to_json(), FEISHU_USER_SECRET_REF)
    store.mark_state("feishu", "user", "verified", account_label="谭凌峰")


def test_parse_app_credentials_accepts_colon_and_whitespace() -> None:
    assert parse_app_credentials("cli_a : s3cret") == ("cli_a", "s3cret")
    assert parse_app_credentials("cli_a s3cret") == ("cli_a", "s3cret")
    with pytest.raises(DomainError):
        parse_app_credentials("only-one-part")


@respx.mock
async def test_tenant_token_is_cached(store: CredentialStore) -> None:
    _seed_app(store)
    route = respx.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    ).mock(
        return_value=httpx.Response(
            200, json={"code": 0, "tenant_access_token": "t-1", "expire": 7200}
        )
    )
    manager = FeishuTokenManager(store)

    assert await manager.tenant_token() == "t-1"
    assert await manager.tenant_token() == "t-1"
    assert route.call_count == 1


@respx.mock
async def test_best_token_prefers_verified_user_channel(store: CredentialStore) -> None:
    _seed_app(store)
    _seed_user(store)
    manager = FeishuTokenManager(store)

    # No HTTP at all: the stored, unexpired user token wins.
    assert await manager.best_token() == "u-old"


@respx.mock
async def test_best_token_falls_back_to_tenant(store: CredentialStore) -> None:
    _seed_app(store)
    respx.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    ).mock(
        return_value=httpx.Response(
            200, json={"code": 0, "tenant_access_token": "t-1", "expire": 7200}
        )
    )
    manager = FeishuTokenManager(store)

    assert await manager.best_token() == "t-1"


@respx.mock
async def test_expired_user_token_is_refreshed(store: CredentialStore) -> None:
    _seed_app(store)
    _seed_user(store, expired=True)
    respx.post("https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal").mock(
        return_value=httpx.Response(
            200, json={"code": 0, "app_access_token": "a-1", "expire": 7200}
        )
    )
    refresh_route = respx.post(
        "https://open.feishu.cn/open-apis/authen/v1/oidc/refresh_access_token"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {"access_token": "u-new", "refresh_token": "ur-new", "expires_in": 7200},
            },
        )
    )
    manager = FeishuTokenManager(store)

    assert await manager.user_token() == "u-new"
    assert json.loads(refresh_route.calls[0].request.content)["refresh_token"] == "ur-old"
    bundle = manager.user_bundle()
    assert bundle is not None and bundle.access_token == "u-new"
    record = store.get("feishu", "user")
    assert record is not None and record.state == "verified"


async def test_require_token_raises_when_nothing_configured(store: CredentialStore) -> None:
    manager = FeishuTokenManager(store)

    with pytest.raises(DomainError) as caught:
        await manager.require_token()
    assert caught.value.code == "FEISHU_LOGIN_REQUIRED"
