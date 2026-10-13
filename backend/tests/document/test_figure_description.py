"""图表语义描述：请求协议、错误分类、与图的对应关系。

**这一组用例能验证什么、不能验证什么。** 没有真实的视觉模型端点可用，所以这里断言的是
**协议**：请求体的形状（`content` 是数组、base64 data URL、`temperature=0`、非流式）、
状态码到错误码的映射、以及"一张图一段描述"的对应关系。**识别质量与真实服务的兼容性
没有被验证过** —— 那需要能跑通的 API Key，报告时不能含糊。
"""
from __future__ import annotations

import base64
import json

import httpx
import pytest

from app.api.errors import DomainError
from app.document.figure_description import (
    INSTRUCTION,
    MAX_IMAGE_BYTES,
    VisionSettings,
    describe_all,
    describe_image,
)

SETTINGS = VisionSettings(base_url="https://vision.test/v1", model="vlm-1", api_key="key-1")

#: 最小的合法 PNG 头，够用来当"图片字节"。
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 200


def _reply(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _ok(description: str = "一张状态转移图"):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": description}}]}
        )

    return handler


@pytest.mark.asyncio
async def test_request_carries_the_image_as_a_base64_data_url() -> None:
    """`content` 必须是数组，图片用 data URL —— OpenAI 兼容的视觉格式就是这样。

    文档里的图没有公网地址，所以只能内联。断言解码回来的字节与输入一致：编码错了
    （比如多一层转义）服务端会当成坏图，而错误只会显示成"不支持看图"。
    """
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "描述"}}]})

    async with _reply(handler) as client:
        assert await describe_image(PNG, settings=SETTINGS, client=client) == "描述"

    body = seen[0]
    assert body["model"] == "vlm-1"
    assert body["stream"] is False
    # 固定为 0：描述要可复现，否则重复导入同一份文档会得到不同的索引内容。
    assert body["temperature"] == 0.0
    parts = body["messages"][0]["content"]
    assert isinstance(parts, list)
    assert parts[0] == {"type": "text", "text": INSTRUCTION}
    url = parts[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == PNG


@pytest.mark.asyncio
async def test_endpoint_and_auth_header_follow_the_configured_base_url() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "描述"}}]})

    async with _reply(handler) as client:
        await describe_image(PNG, settings=SETTINGS, client=client)

    assert str(seen[0].url) == "https://vision.test/v1/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer key-1"


@pytest.mark.asyncio
async def test_a_local_service_without_a_key_sends_no_authorization() -> None:
    """本地服务不需要认证，`Bearer ` 加空值只会是噪声。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "描述"}}]})

    async with _reply(handler) as client:
        await describe_image(
            PNG, settings=VisionSettings(base_url="http://127.0.0.1:11434/v1", model="m"), client=client
        )

    assert "authorization" not in seen[0].headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "FIGURE_DESCRIPTION_AUTH_FAILED"),
        (403, "FIGURE_DESCRIPTION_AUTH_FAILED"),
        (400, "FIGURE_DESCRIPTION_UNSUPPORTED"),
        (404, "FIGURE_DESCRIPTION_UNSUPPORTED"),
        (422, "FIGURE_DESCRIPTION_UNSUPPORTED"),
        (429, "FIGURE_DESCRIPTION_RATE_LIMITED"),
        (500, "FIGURE_DESCRIPTION_UNAVAILABLE"),
        (503, "FIGURE_DESCRIPTION_UNAVAILABLE"),
    ],
)
async def test_status_codes_map_to_figure_specific_errors(status: int, code: str) -> None:
    """错误码是这一层自己的，不复用聊天的 `MODEL_*`。

    聊天的码带着"检查 API Key""修改模型名称"这类面向对话的处置建议，而这里失败只意味着
    "这几张图没有描述" —— 用户通常不该看到，更不该被引去改对话配置。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "nope"})

    async with _reply(handler) as client:
        with pytest.raises(DomainError) as error:
            await describe_image(PNG, settings=SETTINGS, client=client)

    assert error.value.code == code


@pytest.mark.asyncio
async def test_a_malformed_response_is_a_protocol_error_not_an_empty_description() -> None:
    """结构不对要报错，不能返回空串。

    空串会被下游当成"模型说这张图没有内容"，而真相是我们没读懂响应 —— 两者在下游看起来
    一样，但前者会让用户以为功能在工作。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    async with _reply(handler) as client:
        with pytest.raises(DomainError) as error:
            await describe_image(PNG, settings=SETTINGS, client=client)

    assert error.value.code == "FIGURE_DESCRIPTION_PROTOCOL_ERROR"


@pytest.mark.asyncio
async def test_an_oversized_image_is_skipped_without_a_request() -> None:
    """过大的图不发出：请求体会爆炸，模型端多半也会拒。"""
    called = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "描述"}}]})

    async with _reply(handler) as client:
        result = await describe_image(b"x" * (MAX_IMAGE_BYTES + 1), settings=SETTINGS, client=client)

    assert result == ""
    assert called == []


@pytest.mark.asyncio
async def test_descriptions_stay_aligned_with_their_images() -> None:
    """描述与图一一对应，且中间某张失败不影响其余。

    对应关系错了比没有描述更糟：把"状态转移图"的描述挂到"曲线图"上，检索会给出错误依据。
    所以逐张发、按下标装回结果。
    """
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        if counter["n"] == 2:
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json={"choices": [{"message": {"content": f"描述{counter['n']}"}}]})

    async with _reply(handler) as client:
        results = await describe_all([PNG, PNG, PNG], settings=SETTINGS, client=client)

    assert len(results) == 3
    assert results[0] and results[2], "其余两张应当照常拿到描述"
    assert results[1] == "", "失败的那张应当是空串，而不是占用别人的描述"
