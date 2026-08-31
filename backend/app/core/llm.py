from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Protocol

import httpx
from pydantic import Field

from app.api.errors import DomainError
from app.schemas.common import WireModel


class ModelConfig(WireModel):
    preset: str
    base_url: str
    model: str
    timeout_seconds: float = Field(gt=0, le=300)


class ModelConnectionResult(WireModel):
    connected: bool
    latency_ms: int


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
            "temperature": 0,
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
                        if not emitted_content:
                            raise _model_error("MODEL_PROTOCOL_ERROR")
                        return
                    try:
                        data = json.loads(event)
                        choices = data["choices"]
                        delta = choices[0]["delta"]
                    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as error:
                        raise _model_error("MODEL_PROTOCOL_ERROR") from error
                    content = delta.get("content") if isinstance(delta, dict) else None
                    if content is None:
                        continue
                    if not isinstance(content, str):
                        raise _model_error("MODEL_PROTOCOL_ERROR")
                    if content:
                        emitted_content = True
                        yield ChatDelta(content=content)
                raise _model_error("MODEL_PROTOCOL_ERROR")
        except httpx.TimeoutException as error:
            raise _model_error("MODEL_TIMEOUT") from error
        except httpx.HTTPError as error:
            raise _model_error("MODEL_UNAVAILABLE") from error


async def _sse_events(response: httpx.Response) -> AsyncIterator[str]:
    data_lines: list[str] = []
    async for line in response.aiter_lines():
        if not line:
            if data_lines:
                yield "\n".join(data_lines)
                data_lines.clear()
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif line.startswith(("event:", "id:", "retry:", ":")):
            continue
        else:
            raise _model_error("MODEL_PROTOCOL_ERROR")
    if data_lines:
        raise _model_error("MODEL_PROTOCOL_ERROR")


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    if response.status_code in (401, 403):
        raise _model_error("MODEL_AUTH_FAILED")
    if response.status_code == 404:
        raise _model_error("MODEL_NOT_FOUND")
    if response.status_code == 429:
        raise _model_error("MODEL_RATE_LIMITED")
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
