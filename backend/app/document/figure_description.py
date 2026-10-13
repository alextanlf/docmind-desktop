"""用视觉模型描述图表语义。

**为什么需要它。** OCR 补上的是"图里的字"，而有一整类图**根本没有字**：状态转移图、
曲线图、示意图、流程图。实测 12 份课件里 282 张页内插图，其中 **112 张没有图内文字**，
而按位图/矢量拆开看，**92 张是纯光栅图**——它们的语义完全在形状与布局里，任何 OCR 都
读不出来。这一层是唯一能覆盖它们的办法。

**为什么是独立模块，不复用 `app/core/llm.py` 的聊天通路。** 两者要的东西不同：

* 聊天是**流式**的（`stream_chat` 逐个 delta 吐字），图描述是**一次性**的短回答，流式
  只会让它更复杂；
* 聊天消息的 `content` 是纯字符串，而视觉请求要求 `content` 是"文字 + `image_url`"的
  数组 —— 为了塞进视觉而改聊天消息的结构，会把工具调用那条路径也一起搅动；
* 两者的失败语义不同：聊天失败要报给用户，图描述失败只该让这一张图少一段描述。

所以这里自己发请求、自己解析，只共用"OpenAI 兼容"这一件事。

**隐私边界（必须让用户知道）。** 打开这个功能意味着**把文档里的图片发到模型服务商**。
这与"本地优先"是相反的取舍，所以它是显式开关、默认关闭，且文案要写清楚发出去的是什么。
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass

import httpx

from app.api.errors import DomainError

#: 单张图发给模型时的最大字节数。超过就跳过：一张几十 MB 的扫描页会让请求体爆炸，
#: 而且模型端多半也会拒。
MAX_IMAGE_BYTES = 4 * 1024 * 1024
#: 默认超时。图描述是交互之外的富化步骤，可以比聊天宽松一些。
DEFAULT_TIMEOUT_SECONDS = 60.0

#: 要求模型只输出简短描述，不要复述指令、不要加解释。
INSTRUCTION = (
    "用一句中文描述这张图表达的内容，供全文检索使用。"
    "如果图中包含文字、坐标轴、数值或标签，一并写进描述里。"
    "不要复述这句要求，不要加『这张图片显示了』这类开场白，只给描述本身。"
)


@dataclass(frozen=True)
class VisionSettings:
    """发起图描述所需的连接信息。

    与 `app/core/llm.ModelConfig` 分开：那个是"用哪个模型回答对话"，这个是"用哪个模型
    看图"。同一个模型可能只擅长其中一件事，而且图描述应当允许用户单独关掉。
    """

    base_url: str
    model: str
    api_key: str = ""
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS


def _request_body(settings: VisionSettings, image: bytes) -> dict[str, object]:
    """OpenAI 兼容的视觉请求体。

    `content` 必须是数组：文字片段 + 每张图的 `image_url`。`image_url` 用 base64
    data URL —— 模型服务商要能取到图，而文档里的图没有公网地址。
    """
    encoded = base64.b64encode(image).decode("ascii")
    parts: list[dict[str, object]] = [
        {"type": "text", "text": INSTRUCTION},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
    ]
    return {
        "model": settings.model,
        "messages": [{"role": "user", "content": parts}],
        # 描述要可复现：同一个问题不该因为采样而每次给出不同答案，那样重复导入同一份
        # 文档会得到不同的索引内容。
        "temperature": 0.0,
        "stream": False,
    }


def _headers(settings: VisionSettings) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    # 空 token 表示这个服务不需要认证（本地服务），此时发 `Bearer ` 只会是噪声。
    if settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"
    return headers


def _description_error(code: str) -> DomainError:
    """图描述专用的错误码。

    **不复用聊天的 `MODEL_*` 码。** 那些码会带着"检查 API Key""修改模型名称"这类面向
    对话的处置建议冒到界面上，而这里失败只意味着"这几张图没有描述"，用户甚至不该看到
    它 —— 除了诊断。
    """
    errors = {
        "FIGURE_DESCRIPTION_AUTH_FAILED": ("图描述服务认证失败", 401, False),
        "FIGURE_DESCRIPTION_UNSUPPORTED": ("该模型不支持看图", 400, False),
        "FIGURE_DESCRIPTION_RATE_LIMITED": ("图描述服务请求过于频繁", 429, True),
        "FIGURE_DESCRIPTION_TIMEOUT": ("图描述超时", 504, True),
        "FIGURE_DESCRIPTION_UNAVAILABLE": ("图描述服务暂时不可用", 503, True),
        "FIGURE_DESCRIPTION_PROTOCOL_ERROR": ("图描述服务返回了无法识别的数据", 502, False),
    }
    message, status_code, retryable = errors[code]
    return DomainError(code, message, status_code, retryable)


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    if response.status_code in (401, 403):
        raise _description_error("FIGURE_DESCRIPTION_AUTH_FAILED")
    if response.status_code == 429:
        raise _description_error("FIGURE_DESCRIPTION_RATE_LIMITED")
    if response.status_code in (400, 404, 422):
        # 400/404/422 在视觉这条路上最常见的两种原因：模型不支持图片输入，或者模型名
        # 写错了。两者都要用户改配置，所以合成一个"不支持"的码即可。
        raise _description_error("FIGURE_DESCRIPTION_UNSUPPORTED")
    raise _description_error("FIGURE_DESCRIPTION_UNAVAILABLE")


def _extract_text(payload: object) -> str:
    """从 OpenAI 兼容的响应里取第一段回答。

    结构不对时抛协议错误而不是返回空串：空串会被当成"模型说这张图没有内容"，而真相是
    我们没读懂响应 —— 两者在下游看起来一样，但前者会让用户以为功能在工作。
    """
    if not isinstance(payload, dict):
        raise _description_error("FIGURE_DESCRIPTION_PROTOCOL_ERROR")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise _description_error("FIGURE_DESCRIPTION_PROTOCOL_ERROR")
    first = choices[0]
    if not isinstance(first, dict):
        raise _description_error("FIGURE_DESCRIPTION_PROTOCOL_ERROR")
    message = first.get("message")
    if not isinstance(message, dict):
        raise _description_error("FIGURE_DESCRIPTION_PROTOCOL_ERROR")
    content = message.get("content")
    if not isinstance(content, str):
        raise _description_error("FIGURE_DESCRIPTION_PROTOCOL_ERROR")
    return content.strip()


async def describe_image(
    image: bytes,
    *,
    settings: VisionSettings,
    client: httpx.AsyncClient | None = None,
) -> str:
    """给一张图生成一段描述。空图或过大的图返回空串。

    **一次只发一张。** 一次请求塞多张图时，模型只回一段文字，我们无法知道它描述的是哪
    一张 —— 把"状态转移图"的描述挂到"曲线图"上比没有描述更糟：检索会给出错误依据。图
    描述要并进正文参与检索，对应关系必须可靠，所以宁可多花往返。
    """
    if not image or len(image) > MAX_IMAGE_BYTES:
        return ""

    body = _request_body(settings, image)
    url = f"{settings.base_url.rstrip('/')}/chat/completions"
    try:
        if client is not None:
            response = await client.post(url, json=body, headers=_headers(settings))
        else:
            async with httpx.AsyncClient(timeout=settings.timeout_seconds) as owned:
                response = await owned.post(url, json=body, headers=_headers(settings))
    except httpx.TimeoutException as error:
        raise _description_error("FIGURE_DESCRIPTION_TIMEOUT") from error
    except httpx.HTTPError as error:
        raise _description_error("FIGURE_DESCRIPTION_UNAVAILABLE") from error

    _raise_for_status(response)
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError):
        raise _description_error("FIGURE_DESCRIPTION_PROTOCOL_ERROR") from None
    return _extract_text(payload)


async def describe_all(
    images: list[bytes],
    *,
    settings: VisionSettings,
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    """逐张描述一组图，返回与输入等长的列表（失败的为空串）。

    单张失败不中断其余：一张图描述不出来不该让整篇文档的富化停在这里。失败原因不在这里
    区分 —— 调用方只关心"这张图有没有描述"，要诊断就单独调 :func:`describe_image`。
    """
    descriptions: list[str] = []
    for image in images:
        try:
            descriptions.append(await describe_image(image, settings=settings, client=client))
        except DomainError:
            descriptions.append("")
    return descriptions
