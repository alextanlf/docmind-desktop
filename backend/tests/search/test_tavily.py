from __future__ import annotations

import json

import httpx
import pytest

from app.schemas.web_search import SearchRequest
from app.search import tavily as tavily_module
from app.search.provider import SearchProviderError
from app.search.tavily import TavilyProvider


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.tavily.com"
    )


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "results": [{"url": "https://example.test/a", "title": "A", "content": "正文"}]
        },
    )


@pytest.mark.asyncio
async def test_request_asks_for_advanced_depth_and_nothing_more() -> None:
    """🔴 `search_depth=advanced` 是实测定下来的：相关性更好（score 0.79→0.86）、
    `content` 只涨 39%、约 +0.7s。

    这里同时钉死两个**不该出现**的参数：
    - `include_content` 是官方**不存在**的参数，曾长期写在这里静默失效，让"已请求正文"
      看起来成立；
    - `include_raw_content` 是纯噪声（结果集与 `content` 完全相同，只多 35.8K/次的导航
      样板 markdown），agent loop 最多 3 轮 → 单条回答多 ~107K 字符。别顺手加回来。
    """
    seen: dict = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return _ok(request)

    async with _client(handler) as client:
        await TavilyProvider(client=client).search(SearchRequest(query="q", max_results=4))

    assert seen["search_depth"] == "advanced"
    assert seen["max_results"] == 4
    assert "include_content" not in seen
    assert "include_raw_content" not in seen


@pytest.mark.asyncio
async def test_keyless_request_sends_the_access_mode_header_and_no_key() -> None:
    seen: dict = {}

    def handler(request):
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return _ok(request)

    async with _client(handler) as client:
        await TavilyProvider(client=client).search(SearchRequest(query="q"))

    assert seen["headers"]["x-tavily-access-mode"] == "keyless"
    assert "api_key" not in seen["body"]


@pytest.mark.asyncio
async def test_a_stored_key_switches_to_the_keyed_path() -> None:
    seen: dict = {}

    def handler(request):
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return _ok(request)

    async with _client(handler) as client:
        await TavilyProvider("tvly-x", client=client).search(SearchRequest(query="q"))

    assert seen["body"]["api_key"] == "tvly-x"
    assert "x-tavily-access-mode" not in seen["headers"]


@pytest.mark.parametrize(
    ("status", "body", "code", "message"),
    [
        (
            401,
            {"error": {"code": "unauthorized", "message": "keyless 额度已用尽"}},
            "SEARCH_AUTH_FAILED",
            "keyless 额度已用尽",
        ),
        (403, {"detail": {"error": "forbidden"}}, "SEARCH_AUTH_FAILED", "forbidden"),
        (429, {"detail": {"error": "too many"}}, "SEARCH_PROVIDER_RATE_LIMITED", "too many"),
        (500, {"error": {"message": "internal"}}, "SEARCH_PROVIDER_ERROR", "internal"),
        (
            404,
            {
                "error": {
                    "code": "unsupported_endpoint",
                    "message": "This Tavily endpoint requires an API key.",
                }
            },
            "SEARCH_PROVIDER_ERROR",
            "This Tavily endpoint requires an API key.",
        ),
    ],
)
@pytest.mark.asyncio
async def test_status_maps_to_a_code_and_the_vendor_message_is_surfaced(
    status: int, body: dict, code: str, message: str
) -> None:
    """🔴 响应体里的原因必须透出来。

    免密钥打满时 Tavily 返回的是"自然语言指令"，格式与带 Key 的 429 JSON 不同；文案若
    一律写成"暂时不可用"，用户看到的是"服务坏了"，而实际是"免费额度用完了"。
    注意免密钥的错误体是裸 `{"error": {...}}`，**不在 `detail` 下**（实测过）。
    """

    def handler(_request):
        return httpx.Response(status, json=body)

    async with _client(handler) as client:
        with pytest.raises(SearchProviderError) as error:
            await TavilyProvider(client=client).search(SearchRequest(query="q"))

    assert error.value.code == code
    assert message in error.value.message


@pytest.mark.asyncio
async def test_unparseable_error_body_keeps_the_generic_message(monkeypatch) -> None:
    """打满时的响应形态未公开。解析不出来时不能把原文当用户文案（可能是 HTML 错误页），
    「通用文案 + 日志留线索」才是安全的组合。

    ⚠️ 断言挂在 **logger 的调用**上，不挂 `caplog`、也不挂自己挂的 handler：本套件的全局
    logging 状态会被其他测试搅乱 —— 实测同一条断言单跑绿、全量时记录**既没进 `caplog`
    也没进直接挂在模块 logger 上的 handler**（`propagate=True`、记录确实产生）。这里要证的
    是"原文被写进了日志"这件事本身，不是 logging 基础设施当时恰好处于什么状态。
    """
    logged: list[str] = []

    def _record(message: str, *args: object) -> None:
        logged.append(message % args if args else message)

    monkeypatch.setattr(tavily_module.logger, "warning", _record)

    def _handler(_request):
        return httpx.Response(502, text="<html>Bad Gateway</html>")

    async with _client(_handler) as client:
        with pytest.raises(SearchProviderError) as error:
            await TavilyProvider(client=client).search(SearchRequest(query="q"))

    assert error.value.code == "SEARCH_PROVIDER_ERROR"
    # 原文绝不进用户文案 —— 那可能是 HTML 错误页。
    assert "<html>" not in error.value.message
    # 但必须留在日志里，否则 keyless 打满这种"格式未公开"的情况根本无从排查。
    assert logged, "解析失败时必须记日志，否则线上无从判断到底发生了什么"
    assert "Bad Gateway" in logged[0]
