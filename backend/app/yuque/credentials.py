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
            label="语雀网页登录",
            has_secret=False,
            purpose="source",
            summary="在浏览器里登录语雀，直接读取你的知识库",
            icon="login",
            keywords=("网页", "浏览器", "扫码", "web"),
            hint="浏览器里直接登录，不用申请令牌；导入与同步时会打开 Chrome。",
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
            purpose="source",
            summary="用个人访问令牌读取语雀知识库",
            icon="key",
            keywords=("令牌", "token", "api", "个人访问令牌"),
            hint="更快更稳，但要先去语雀后台申请一个个人访问令牌。",
        ),
    ),
)
