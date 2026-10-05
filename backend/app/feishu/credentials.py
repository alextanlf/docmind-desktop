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

from app.feishu import client
from app.feishu.tokens import FEISHU_APP_SECRET_REF, parse_app_credentials
from app.feishu.webhook import probe_feishu_webhook
from app.remote.credentials import CredentialChannelSpec, ProviderCredentialSpec


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
        ),
    ),
    login_channel="user",
)


def _mask_app_id(app_id: str) -> str:
    if len(app_id) <= 6:
        return app_id
    return f"{app_id[:4]}***{app_id[-2:]}"
