from __future__ import annotations

import httpx
import pytest

from app.api.errors import DomainError
from app.feishu.webhook import normalize_feishu_webhook, probe_feishu_webhook

VALID = "https://open.feishu.cn/open-apis/bot/v2/hook/abc123"


class TestNormalizeFeishuWebhook:
    def test_accepts_both_official_hosts(self) -> None:
        assert normalize_feishu_webhook(VALID) == VALID
        lark = "https://open.larksuite.com/open-apis/bot/v2/hook/abc123"
        assert normalize_feishu_webhook(lark) == lark

    def test_blank_input_stays_blank(self) -> None:
        assert normalize_feishu_webhook("   ") == ""

    @pytest.mark.parametrize(
        "value",
        [
            "http://open.feishu.cn/open-apis/bot/v2/hook/abc",  # not https
            "https://evil.example/open-apis/bot/v2/hook/abc",  # wrong host
            "https://user:pw@open.feishu.cn/open-apis/bot/v2/hook/a",  # userinfo
            "https://open.feishu.cn:8443/open-apis/bot/v2/hook/a",  # odd port
            "https://open.feishu.cn/open-apis/other/abc",  # wrong path
        ],
    )
    def test_rejects_anything_outside_the_bot_endpoint(self, value: str) -> None:
        # The allow-list is what stops a hand-edited settings entry from turning
        # the probe into an SSRF primitive.
        with pytest.raises(DomainError) as raised:
            normalize_feishu_webhook(value)
        assert raised.value.code == "FEISHU_WEBHOOK_INVALID"


class TestProbeFeishuWebhook:
    async def test_sends_the_verification_message(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"code": 0})

        await probe_feishu_webhook(VALID, transport=httpx.MockTransport(handler))

        assert len(seen) == 1
        assert seen[0].url == VALID
        assert b"msg_type" in seen[0].content

    @pytest.mark.parametrize("payload", [{}, {"code": 0}, {"StatusCode": 0}, {"code": None}])
    async def test_accepts_the_success_shapes_feishu_actually_returns(
        self, payload: dict
    ) -> None:
        transport = httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
        await probe_feishu_webhook(VALID, transport=transport)

    @pytest.mark.parametrize(
        "payload", [{"code": 19001}, {"StatusCode": 19001}, {"code": 1, "StatusCode": 0}]
    )
    async def test_rejects_an_error_code_inside_a_200(self, payload: dict) -> None:
        transport = httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
        with pytest.raises(DomainError) as raised:
            await probe_feishu_webhook(VALID, transport=transport)
        assert raised.value.code == "FEISHU_AUTH_FAILED"

    async def test_maps_http_errors_to_an_unavailable_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        with pytest.raises(DomainError) as raised:
            await probe_feishu_webhook(VALID, transport=httpx.MockTransport(handler))
        assert raised.value.code == "FEISHU_UNAVAILABLE"
        assert raised.value.retryable is True

    async def test_maps_an_http_error_status_to_an_auth_failure(self) -> None:
        transport = httpx.MockTransport(lambda _: httpx.Response(404))
        with pytest.raises(DomainError) as raised:
            await probe_feishu_webhook(VALID, transport=transport)
        assert raised.value.code == "FEISHU_AUTH_FAILED"
        assert raised.value.retryable is False

    @pytest.mark.parametrize("body", [b"not json", b"[1,2]"])
    async def test_rejects_a_non_object_body(self, body: bytes) -> None:
        transport = httpx.MockTransport(lambda _: httpx.Response(200, content=body))
        with pytest.raises(DomainError) as raised:
            await probe_feishu_webhook(VALID, transport=transport)
        assert raised.value.code == "FEISHU_PROTOCOL_ERROR"
