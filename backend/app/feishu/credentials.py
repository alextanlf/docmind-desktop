"""Feishu credential declaration.

Two channels:

* ``app``  — self-built app credentials (``App ID:App Secret``), a secret
  channel verified by exchanging a tenant_access_token. Sufficient on its
  own for documents shared with the app.
* ``user`` — OAuth user authorization, an interactive channel driven by the
  provider's ``begin_login`` loopback flow (no user-entered secret).

The spec's ``login_channel`` points at ``user`` so the generic login
endpoints mark the right row.
"""
from __future__ import annotations

from app.api.errors import DomainError
from app.feishu import client
from app.feishu.tokens import FEISHU_APP_SECRET_REF, parse_app_credentials
from app.feishu.webhook import (
    normalize_feishu_webhook,
    post_feishu_webhook_text,
    probe_feishu_webhook,
)
from app.remote.credentials import (
    CredentialChannelSpec,
    CredentialStore,
    ProviderCredentialSpec,
)
from app.remote.notifications import (
    NOTIFY_ERROR,
    NOTIFY_INFO,
    NOTIFY_SUCCESS,
    NotificationEvent,
    NotificationTarget,
)
from app.storage.models import ProviderCredentialState


async def test_feishu_app_credentials(secret: str) -> str | None:
    """Verify app credentials by exchanging them for a tenant token."""
    app_id, app_secret = parse_app_credentials(secret)
    await client.fetch_tenant_access_token(app_id, app_secret)
    return _mask_app_id(app_id)


FEISHU_WEBHOOK_SECRET_REF = "feishu:webhook"


async def test_feishu_webhook(webhook_url: str) -> str | None:
    """Verify a custom-bot webhook by sending the binding-verification message."""
    await probe_feishu_webhook(webhook_url)
    return None


FEISHU_CREDENTIAL_SPEC = ProviderCredentialSpec(
    provider="feishu",
    channels=(
        CredentialChannelSpec(
            name="app",
            label="飞书自建应用",
            has_secret=True,
            default_secret_ref=FEISHU_APP_SECRET_REF,
            tester=test_feishu_app_credentials,
        ),
        CredentialChannelSpec(
            name="user",
            label="飞书账号授权",
            has_secret=False,
        ),
        CredentialChannelSpec(
            name="webhook",
            label="飞书机器人",
            has_secret=True,
            default_secret_ref=FEISHU_WEBHOOK_SECRET_REF,
            tester=test_feishu_webhook,
            # The webhook doubles as the notification target, and `test` posts
            # to it verbatim. Without the host whitelist the generic save path
            # would happily store (and then POST to) any URL the user typed.
            normalizer=normalize_feishu_webhook,
            secret_placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/…",
            help_url="https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot",
            help_label="添加机器人",
        ),
    ),
    login_channel="user",
)


def build_feishu_notification_target(
    credential_store: CredentialStore,
    *,
    channel: str = "webhook",
) -> NotificationTarget:
    """Wire the bound bot webhook up as a notification destination.

    The target resolves the secret at delivery time rather than closing over
    it, so unbinding the channel immediately stops notifications without any
    extra bookkeeping. Readiness mirrors the verified state the UI already
    shows, which keeps "connected" and "will notify" the same truth.
    """
    def _secret() -> str | None:
        record = credential_store.get("feishu", channel)
        if record is None or record.state != ProviderCredentialState.VERIFIED.value:
            return None
        return credential_store.secret_for("feishu", channel)

    async def _send(event: NotificationEvent) -> None:
        webhook_url = _secret()
        if webhook_url is None:
            # The binding went away between the readiness probe and delivery.
            raise DomainError("FEISHU_WEBHOOK_REQUIRED", "飞书机器人已解除绑定", 400, False)
        text = event.title if not event.body else f"{event.title}\n{event.body}"
        await post_feishu_webhook_text(webhook_url, text)

    def _is_ready() -> bool:
        return _secret() is not None

    return NotificationTarget(
        name="feishu:webhook",
        send=_send,
        is_ready=_is_ready,
        severities=frozenset({NOTIFY_INFO, NOTIFY_SUCCESS, NOTIFY_ERROR}),
    )


def _mask_app_id(app_id: str) -> str:
    if len(app_id) <= 6:
        return app_id
    return f"{app_id[:4]}***{app_id[-2:]}"
