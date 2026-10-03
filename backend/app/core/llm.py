from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import Field, field_validator

from app.api.errors import DomainError
from app.schemas.common import WireModel


class ModelConfig(WireModel):
    preset: str
    base_url: str
    model: str
    timeout_seconds: float = Field(gt=0, le=300)

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        if value == "":
            return value
        if value != value.strip() or any(character.isspace() for character in value):
            raise ValueError("invalid model base URL")
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as error:
            raise ValueError("invalid model base URL") from error
        scheme = parsed.scheme.lower()
        if (
            scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or "?" in value
            or parsed.fragment
            or "#" in value
        ):
            raise ValueError("invalid model base URL")
        host = parsed.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
        netloc = host if port is None or default_port else f"{host}:{port}"
        return urlunsplit((scheme, netloc, parsed.path.rstrip("/"), "", ""))


class ModelConnectionResult(WireModel):
    connected: bool
    latency_ms: int


# Vendors disagree on the valid temperature range: Zhipu documents (0, 1) and
# explicitly rejects `temperature = 0`, while DeepSeek/Qwen/Moonshot accept 0.
# Probing the connection therefore uses the smallest value every known vendor
# accepts instead of an absolute zero.
CONNECTION_TEST_TEMPERATURE = 0.1


class LLMMessage(WireModel):
    role: str
    content: str


class ChatRequest(WireModel):
    messages: list[LLMMessage]
    temperature: float = 0.2


class ChatDelta(WireModel):
    content: str


class LLMProvider(Protocol):
    async def test_connection(self) -> ModelConnectionResult: ...

    def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatDelta]: ...


class OpenAICompatibleProvider:
    def __init__(self, config: ModelConfig, api_key: str) -> None:
        self.config = config
        self.api_key = api_key

    @property
    def _url(self) -> str:
        return f"{self.config.base_url.rstrip('/')}/chat/completions"

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    async def test_connection(self) -> ModelConnectionResult:
        payload = {
            "model": self.config.model,
            "messages": [{"role": "user", "content": "请回复“连接成功”。"}],
            "temperature": CONNECTION_TEST_TEMPERATURE,
            "stream": False,
        }
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                response = await client.post(self._url, json=payload, headers=self._headers)
        except httpx.TimeoutException as error:
            raise _model_error("MODEL_TIMEOUT") from error
        except httpx.HTTPError as error:
            raise _model_error("MODEL_UNAVAILABLE") from error
        _raise_for_status(response)
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (IndexError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise _model_error("MODEL_PROTOCOL_ERROR") from error
        if not isinstance(content, str) or not content:
            raise _model_error("MODEL_PROTOCOL_ERROR")
        return ModelConnectionResult(connected=True, latency_ms=round((time.perf_counter() - started) * 1000))

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatDelta]:
        payload = {
            "model": self.config.model,
            "messages": [message.model_dump() for message in request.messages],
            "temperature": request.temperature,
            "stream": True,
        }
        try:
            async with (
                httpx.AsyncClient(timeout=self.config.timeout_seconds) as client,
                client.stream("POST", self._url, json=payload, headers=self._headers) as response,
            ):
                _raise_for_status(response)
                if not response.headers.get("content-type", "").lower().startswith("text/event-stream"):
                    raise _model_error("MODEL_PROTOCOL_ERROR")
                emitted_content = False
                async for event in _sse_events(response):
                    if event == "[DONE]":
                        break
                    content = _delta_content(event)
                    if content is _NO_CONTENT:
                        # Structurally textless frame (usage-only, reasoning-only,
                        # or a shape we do not recognize): vendors legitimately
                        # emit all three mid-stream.
                        continue
                    if not isinstance(content, str):
                        # `content` is present but not a string: corrupt, not a
                        # frame we can skip over.
                        raise _model_error("MODEL_PROTOCOL_ERROR")
                    if content:
                        emitted_content = True
                        yield ChatDelta(content=content)
                # A stream that never produced a text delta is unusable, whether or
                # not the vendor closed it with the `[DONE]` sentinel.
                if not emitted_content:
                    raise _model_error("MODEL_PROTOCOL_ERROR")
        except httpx.TimeoutException as error:
            raise _model_error("MODEL_TIMEOUT") from error
        except httpx.HTTPError as error:
            raise _model_error("MODEL_UNAVAILABLE") from error


# Marks an SSE frame that carries no text delta at all, as opposed to one that
# carries a `content` value of an unexpected type.
_NO_CONTENT = object()


def _delta_content(event: str) -> object:
    """Extract text from one SSE `data:` payload, or `_NO_CONTENT` when textless.

    Reasoning models (GLM thinking, Kimi, MiMo) interleave `reasoning_content`
    and usage-only frames into the stream, and some vendors close the connection
    without a `[DONE]` sentinel. Those frames are textless rather than corrupt, so
    they are reported as `_NO_CONTENT`. A `content` value that is present but not a
    string is returned as-is so the caller can reject it as corrupt.
    """
    try:
        data = json.loads(event)
    except (json.JSONDecodeError, TypeError):
        return _NO_CONTENT
    if not isinstance(data, dict):
        return _NO_CONTENT
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return _NO_CONTENT
    first = choices[0]
    if not isinstance(first, dict):
        return _NO_CONTENT
    delta = first.get("delta")
    if not isinstance(delta, dict):
        return _NO_CONTENT
    content = delta.get("content")
    if content is None or content == "":
        # Absent, null and empty-string content are all "no text this frame".
        return _NO_CONTENT
    return content


async def _sse_events(response: httpx.Response) -> AsyncIterator[str]:
    data_lines: list[str] = []
    async for raw_line in response.aiter_lines():
        line = raw_line.rstrip("\r")
        if not line:
            if data_lines:
                yield "\n".join(data_lines)
                data_lines.clear()
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif line.startswith(("event:", "id:", "retry:", ":")):
            # Named events, ids, retry hints and `:comment` keep-alives carry no
            # payload for us and are legal SSE.
            continue
        else:
            raise _model_error("MODEL_PROTOCOL_ERROR")
    if data_lines:
        # The vendor closed the connection without the trailing blank line that
        # would have flushed the last event. Emit it rather than discarding
        # content the model already produced.
        yield "\n".join(data_lines)


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    if response.status_code in (401, 403):
        raise _model_error("MODEL_AUTH_FAILED")
    if response.status_code == 404:
        raise _model_error("MODEL_NOT_FOUND")
    if response.status_code == 429:
        raise _model_error("MODEL_RATE_LIMITED")
    if 400 <= response.status_code <= 499:
        raise _model_error("MODEL_PROTOCOL_ERROR")
    if 500 <= response.status_code <= 599:
        raise _model_error("MODEL_UNAVAILABLE")
    raise _model_error("MODEL_UNAVAILABLE")


def _model_error(code: str) -> DomainError:
    errors = {
        "MODEL_AUTH_FAILED": ("模型服务认证失败，请检查 API Key", 401, False, "检查 API Key 后重试"),
        "MODEL_NOT_FOUND": ("未找到指定模型，请检查模型名称", 404, False, "修改模型名称后重试"),
        "MODEL_RATE_LIMITED": ("模型服务请求过于频繁，请稍后重试", 429, True, "稍后重试"),
        "MODEL_TIMEOUT": ("模型服务响应超时，请稍后重试", 504, True, "检查网络或增大超时时间"),
        "MODEL_PROTOCOL_ERROR": ("模型服务返回了无法识别的数据", 502, False, "检查兼容接口配置"),
        "MODEL_UNAVAILABLE": ("模型服务暂时不可用，请稍后重试", 503, True, "稍后重试"),
    }
    message, status_code, retryable, action = errors[code]
    return DomainError(code, message, status_code, retryable, action)
