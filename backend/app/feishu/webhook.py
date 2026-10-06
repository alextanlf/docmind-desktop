"""Feishu custom-bot webhook validation and probing.

This lives in the platform package rather than ``app/api/settings.py`` so the
API layer stays free of platform protocol details: the ``msg_type`` payload
shape, the dual ``code``/``StatusCode`` success fields, and the allowed webhook
hosts are all Feishu specifics that belong next to the rest of the client.
"""

from urllib.parse import urlparse

import httpx

from app.api.errors import DomainError

_ALLOWED_HOSTS = {"open.feishu.cn", "open.larksuite.com"}
_REQUIRED_PATH_PREFIX = "/open-apis/bot/v2/hook/"
_PROBE_TIMEOUT_SECONDS = 15.0


def normalize_feishu_webhook(value: str) -> str:
    """Validate a custom-bot webhook URL and return its canonical form.

    Raises ``FEISHU_WEBHOOK_INVALID`` when the URL points anywhere other than the
    official Feishu bot endpoint, which keeps the probe from being turned into an
    SSRF primitive by a hand-edited settings entry.
    """
    candidate = value.strip()
    if not candidate:
        return ""
    parsed = urlparse(candidate)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _ALLOWED_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
        or not parsed.path.startswith(_REQUIRED_PATH_PREFIX)
    ):
        raise DomainError(
            "FEISHU_WEBHOOK_INVALID",
            "飞书 Webhook 地址无效，请使用飞书自定义机器人的 Webhook",
            422,
        )
    return parsed.geturl()


async def post_feishu_webhook_text(
    webhook_url: str,
    text: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Send one plain-text custom-bot message.

    Shared by the binding probe and real notifications so both agree on the
    payload shape and on what counts as a successful delivery (Feishu answers
    200 with either ``code`` or ``StatusCode``; both must be 0/None).
    """
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_PROBE_TIMEOUT_SECONDS),
            follow_redirects=False,
            transport=transport,
        ) as client:
            response = await client.post(
                webhook_url,
                json={"msg_type": "text", "content": {"text": text}},
            )
    except httpx.HTTPError as error:
        raise DomainError(
            "FEISHU_UNAVAILABLE", "无法连接飞书，请检查网络后重试", 503, True
        ) from error
    if response.status_code >= 400:
        raise DomainError("FEISHU_AUTH_FAILED", "飞书 Webhook 无效或已失效", 401, False)
    try:
        payload = response.json()
    except ValueError as error:
        raise DomainError("FEISHU_PROTOCOL_ERROR", "飞书返回的数据格式无效", 502, True) from error
    if not isinstance(payload, dict):
        raise DomainError("FEISHU_PROTOCOL_ERROR", "飞书返回的数据格式无效", 502, True)
    # Feishu answers 200 with either `code` or `StatusCode`; both must be 0/None.
    code = payload.get("code")
    status_code = payload.get("StatusCode")
    if code not in {None, 0} or status_code not in {None, 0}:
        raise DomainError("FEISHU_AUTH_FAILED", "飞书 Webhook 无效或已失效", 401, False)


async def probe_feishu_webhook(
    webhook_url: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Send the binding-verification message, raising a DomainError on failure.

    ``transport`` exists so tests can drive the four response shapes without a
    network round trip.
    """
    await post_feishu_webhook_text(
        webhook_url, "DocMind 飞书绑定验证成功", transport=transport
    )
