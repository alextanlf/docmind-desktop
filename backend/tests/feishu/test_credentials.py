from __future__ import annotations

import json
from functools import partial
from unittest import mock

import httpx
import pytest
import respx

from app.api.errors import DomainError
from app.core.secrets import MemorySecretStore
from app.feishu.credentials import (
    FEISHU_CREDENTIAL_SPEC,
    build_feishu_notification_target,
)
from app.feishu.credentials import test_feishu_app_credentials as app_credential_tester
from app.feishu.webhook import post_feishu_webhook_text
from app.remote.credentials import CredentialStore
from app.remote.notifications import (
    NotificationEvent,
    NotificationHub,
    NotificationTarget,
)
from app.storage.database import Database


def test_spec_declares_app_and_user_channels_with_user_login_channel() -> None:
    assert FEISHU_CREDENTIAL_SPEC.provider == "feishu"
    assert FEISHU_CREDENTIAL_SPEC.login_channel == "user"

    app = FEISHU_CREDENTIAL_SPEC.channel("app")
    assert app is not None and app.has_secret is True
    assert app.default_secret_ref == "feishu:app-credentials"
    assert app.tester is not None

    user = FEISHU_CREDENTIAL_SPEC.channel("user")
    assert user is not None and user.has_secret is False


@respx.mock
async def test_app_credential_tester_masks_the_app_id() -> None:
    respx.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal").mock(
        return_value=httpx.Response(
            200, json={"code": 0, "tenant_access_token": "t-1", "expire": 7200}
        )
    )

    label = await app_credential_tester("cli_a1b2c3:s3cret")

    assert label == "cli_***c3"


@respx.mock
async def test_app_credential_tester_rejects_invalid_secret() -> None:
    respx.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal").mock(
        return_value=httpx.Response(200, json={"code": 10003, "msg": "invalid app_secret"})
    )

    with pytest.raises(DomainError):
        await app_credential_tester("cli_a1b2c3:wrong")


class TestFeishuWebhookChannel:
    """The webhook channel used to be wired entirely inside api/settings.py, so
    its keychain entry name had a second definition outside the provider spec."""

    def test_webhook_channel_is_declared_in_the_spec(self) -> None:
        channel = FEISHU_CREDENTIAL_SPEC.channel("webhook")
        assert channel is not None
        assert channel.has_secret is True
        assert channel.default_secret_ref == "feishu:webhook"
        assert channel.tester is not None

    def test_webhook_channel_declares_the_host_allow_list_guard(self) -> None:
        """The webhook is posted to verbatim by ``test``, so the channel itself
        must reject foreign hosts.

        When the channel carried no normalizer, the generic
        ``PUT /credentials/{channel}`` endpoint stripped whatever it was given
        and stored it, so any URL became a live request-forgery target.
        """
        channel = FEISHU_CREDENTIAL_SPEC.channel("webhook")
        assert channel is not None
        assert channel.normalizer is not None

    def test_normalizer_rejects_a_non_feishu_host(self) -> None:
        channel = FEISHU_CREDENTIAL_SPEC.channel("webhook")
        assert channel is not None and channel.normalizer is not None

        for hostile in (
            "https://attacker.example.com/steal",
            "http://127.0.0.1:8080/internal-admin",
            "https://open.feishu.cn.evil.com/open-apis/bot/v2/hook/x",
        ):
            with pytest.raises(DomainError):
                channel.normalizer(hostile)

    def test_normalizer_accepts_a_genuine_feishu_webhook(self) -> None:
        channel = FEISHU_CREDENTIAL_SPEC.channel("webhook")
        assert channel is not None and channel.normalizer is not None

        valid = "https://open.feishu.cn/open-apis/bot/v2/hook/abc123"

        assert channel.normalizer(f"  {valid}  ") == valid

    def test_login_channel_is_still_the_user_channel(self) -> None:
        assert FEISHU_CREDENTIAL_SPEC.login_channel == "user"

    def test_channels_keep_their_declared_order(self) -> None:
        assert [c.name for c in FEISHU_CREDENTIAL_SPEC.channels] == ["app", "user", "webhook"]


class TestFeishuNotificationTarget:
    """The bot webhook is only useful if something actually sends to it.

    Before this existed the binding stored a URL and posted one verification
    message; no business event ever reached the bot.
    """

    @staticmethod
    def _verified_store(webhook: str) -> tuple[CredentialStore, MemorySecretStore]:
        secrets = MemorySecretStore()
        db = Database("sqlite+pysqlite:///:memory:")
        db.upgrade()
        store = CredentialStore(db, secrets)
        store.save_secret("feishu", "webhook", webhook, "feishu:webhook")
        store.mark_state("feishu", "webhook", "verified")
        return store, secrets

    async def test_delivers_a_real_notification_to_the_bound_webhook(self) -> None:
        webhook = "https://open.feishu.cn/open-apis/bot/v2/hook/abc123"
        store, _ = self._verified_store(webhook)
        sent: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append(request)
            return httpx.Response(200, json={"code": 0})

        # Intercept at the transport layer so the real target, the real text
        # assembly and the real payload shape are all exercised.
        with mock.patch(
            "app.feishu.credentials.post_feishu_webhook_text",
            new=partial(post_feishu_webhook_text, transport=_FakeTransport(handler)),
        ):
            target = build_feishu_notification_target(store)
            reached = await _hub_with(target).notify(
                NotificationEvent(title="文档导入完成", body="季度报告.pdf")
            )

        assert reached == ["feishu:webhook"]
        assert len(sent) == 1
        payload = json.loads(sent[0].content)
        assert payload["msg_type"] == "text"
        assert "文档导入完成" in payload["content"]["text"]
        assert "季度报告.pdf" in payload["content"]["text"]

    async def test_an_unverified_binding_receives_nothing(self) -> None:
        """Saved but not yet tested must not notify — the UI says the same."""
        secrets = MemorySecretStore()
        db = Database("sqlite+pysqlite:///:memory:")
        db.upgrade()
        store = CredentialStore(db, secrets)
        store.save_secret(
            "feishu", "webhook", "https://open.feishu.cn/open-apis/bot/v2/hook/x", "feishu:webhook"
        )
        target = build_feishu_notification_target(store)

        assert target.is_ready() is False
        assert await _hub_with(target).notify(NotificationEvent(title="导入完成")) == []

    async def test_unbinding_stops_notifications_immediately(self) -> None:
        """The target resolves the secret per delivery, so revocation is live."""
        webhook = "https://open.feishu.cn/open-apis/bot/v2/hook/abc123"
        store, _ = self._verified_store(webhook)
        target = build_feishu_notification_target(store)
        assert target.is_ready() is True

        store.clear("feishu", "webhook")

        assert target.is_ready() is False

    async def test_send_raises_when_the_binding_disappears_mid_flight(self) -> None:
        webhook = "https://open.feishu.cn/open-apis/bot/v2/hook/abc123"
        store, _ = self._verified_store(webhook)
        target = build_feishu_notification_target(store)
        # Simulate the user unbinding after the readiness probe passed.
        store.clear("feishu", "webhook")

        with pytest.raises(DomainError) as raised:
            await target.send(NotificationEvent(title="导入完成"))

        assert raised.value.code == "FEISHU_WEBHOOK_REQUIRED"


class _FakeTransport(httpx.AsyncBaseTransport):
    """Route every request through a sync handler, avoiding real network."""

    def __init__(self, handler) -> None:
        self._handler = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return self._handler(request)


def _hub_with(target: NotificationTarget) -> NotificationHub:
    hub = NotificationHub()
    hub.register(target)
    return hub
