"""Yuque-specific credential declaration.

The spec is the only Yuque-specific piece the registry needs: it declares the
two credential channels (browser session + API token), where the API token
lives in the keychain, and how to verify a token. Everything else (state
tracking, probes, API surface) is generic.
"""
from __future__ import annotations

from app.remote.credentials import CredentialChannelSpec, ProviderCredentialSpec
from app.yuque.api_gateway import YuqueApiGateway

YUQUE_API_SECRET_REF = "yuque-api:token"


async def test_yuque_api_token(token: str) -> str | None:
    """Verify a personal access token against the Yuque Open API."""
    result = await YuqueApiGateway(lambda: token).begin_login()
    return result.account_label


YUQUE_CREDENTIAL_SPEC = ProviderCredentialSpec(
    provider="yuque",
    channels=(
        CredentialChannelSpec(
            name="web",
            label="语雀网页",
            has_secret=False,
        ),
        CredentialChannelSpec(
            name="api",
            label="语雀 API",
            has_secret=True,
            default_secret_ref=YUQUE_API_SECRET_REF,
            tester=test_yuque_api_token,
            secret_placeholder="粘贴语雀个人访问令牌",
            help_url="https://www.yuque.com/yuque/developer/api",
            help_label="获取令牌",
        ),
    ),
)
