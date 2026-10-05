from __future__ import annotations

import httpx
import pytest
import respx

from app.api.errors import DomainError
from app.feishu.credentials import FEISHU_CREDENTIAL_SPEC
from app.feishu.credentials import test_feishu_app_credentials as app_credential_tester


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

    def test_login_channel_is_still_the_user_channel(self) -> None:
        assert FEISHU_CREDENTIAL_SPEC.login_channel == "user"

    def test_channels_keep_their_declared_order(self) -> None:
        assert [c.name for c in FEISHU_CREDENTIAL_SPEC.channels] == ["app", "user", "webhook"]
