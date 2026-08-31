from __future__ import annotations

import json
import logging

import httpx
import pytest
import respx

from app.api.errors import DomainError
from app.core.llm import ChatRequest, LLMMessage, ModelConfig, OpenAICompatibleProvider


@pytest.fixture
def config() -> ModelConfig:
    return ModelConfig(
        preset="custom",
        base_url="https://example.test/v1",
        model="example-model",
        timeout_seconds=12,
    )


@pytest.fixture
def chat_request() -> ChatRequest:
    return ChatRequest(messages=[LLMMessage(role="user", content="请总结文档")], temperature=0.3)


@respx.mock
async def test_openai_compatible_provider_uses_exact_stream_request_shape(
    config: ModelConfig, chat_request: ChatRequest
) -> None:
    route = respx.post("https://example.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"文档回答"}}]}\n\ndata: [DONE]\n\n',
            headers={"content-type": "text/event-stream"},
        )
    )

    provider = OpenAICompatibleProvider(config, "test-key")
    deltas = [delta async for delta in provider.stream_chat(chat_request)]

    assert [delta.content for delta in deltas] == ["文档回答"]
    sent = route.calls[0].request
    assert dict(sent.headers)["authorization"] == "Bearer test-key"
    assert dict(sent.headers)["content-type"] == "application/json"
    assert json.loads(sent.content) == {
        "model": "example-model",
        "messages": [{"role": "user", "content": "请总结文档"}],
        "temperature": 0.3,
        "stream": True,
    }


@respx.mock
async def test_openai_compatible_provider_handles_fragmented_sse_events(
    config: ModelConfig, chat_request: ChatRequest
) -> None:
    async def chunks():
        yield 'data: {"choices":[{"delta":{"content":"文'.encode()
        yield '档"}}]}\n\ndata: {"choices":[{"delta":{"content":"回答"}}]}\n\n'.encode()
        yield b"data: [DONE]\n\n"

    respx.post("https://example.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=chunks(), headers={"content-type": "text/event-stream"})
    )

    deltas = [delta async for delta in OpenAICompatibleProvider(config, "test-key").stream_chat(chat_request)]

    assert [delta.content for delta in deltas] == ["文档", "回答"]


@pytest.mark.parametrize(
    ("response", "code", "retryable", "message"),
    [
        (httpx.Response(400), "MODEL_PROTOCOL_ERROR", False, "模型服务返回了无法识别的数据"),
        (httpx.Response(401), "MODEL_AUTH_FAILED", False, "模型服务认证失败，请检查 API Key"),
        (httpx.Response(403), "MODEL_AUTH_FAILED", False, "模型服务认证失败，请检查 API Key"),
        (httpx.Response(404), "MODEL_NOT_FOUND", False, "未找到指定模型，请检查模型名称"),
        (httpx.Response(415), "MODEL_PROTOCOL_ERROR", False, "模型服务返回了无法识别的数据"),
        (httpx.Response(429), "MODEL_RATE_LIMITED", True, "模型服务请求过于频繁，请稍后重试"),
        (httpx.Response(500), "MODEL_UNAVAILABLE", True, "模型服务暂时不可用，请稍后重试"),
        (httpx.Response(502), "MODEL_UNAVAILABLE", True, "模型服务暂时不可用，请稍后重试"),
        (httpx.Response(503), "MODEL_UNAVAILABLE", True, "模型服务暂时不可用，请稍后重试"),
        (httpx.Response(504), "MODEL_UNAVAILABLE", True, "模型服务暂时不可用，请稍后重试"),
    ],
)
@respx.mock
async def test_openai_compatible_provider_maps_http_failures(
    config: ModelConfig,
    chat_request: ChatRequest,
    response: httpx.Response,
    code: str,
    retryable: bool,
    message: str,
) -> None:
    respx.post("https://example.test/v1/chat/completions").mock(return_value=response)

    with pytest.raises(DomainError) as error:
        _ = [delta async for delta in OpenAICompatibleProvider(config, "test-key").stream_chat(chat_request)]

    assert error.value.code == code
    assert error.value.retryable is retryable
    assert error.value.message == message


@respx.mock
async def test_openai_compatible_provider_maps_timeout_without_exposing_key(
    caplog: pytest.LogCaptureFixture, config: ModelConfig, chat_request: ChatRequest
) -> None:
    respx.post("https://example.test/v1/chat/completions").mock(
        side_effect=httpx.ReadTimeout("timed out")
    )

    with caplog.at_level(logging.DEBUG), pytest.raises(DomainError) as error:
        _ = [delta async for delta in OpenAICompatibleProvider(config, "secret-value").stream_chat(chat_request)]

    assert error.value.code == "MODEL_TIMEOUT"
    assert error.value.retryable is True
    assert error.value.message == "模型服务响应超时，请稍后重试"
    assert "secret-value" not in caplog.text
    assert "Authorization" not in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        "not an event stream\n\n",
        "data: not-json\n\n",
        'data: {"choices":[]}\n\ndata: [DONE]\n\n',
        'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\ndata: [DONE]\n\n',
    ],
)
@respx.mock
async def test_openai_compatible_provider_rejects_invalid_or_empty_sse(
    config: ModelConfig, chat_request: ChatRequest, body: str
) -> None:
    respx.post("https://example.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
    )

    with pytest.raises(DomainError) as error:
        _ = [delta async for delta in OpenAICompatibleProvider(config, "test-key").stream_chat(chat_request)]

    assert error.value.code == "MODEL_PROTOCOL_ERROR"
    assert error.value.retryable is False
    assert error.value.message == "模型服务返回了无法识别的数据"


@respx.mock
async def test_model_connection_uses_non_streaming_minimal_prompt_and_reports_latency(
    config: ModelConfig
) -> None:
    route = respx.post("https://example.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "连接成功"}}]})
    )

    result = await OpenAICompatibleProvider(config, "test-key").test_connection()

    assert result.connected is True
    assert result.latency_ms >= 0
    assert json.loads(route.calls[0].request.content) == {
        "model": "example-model",
        "messages": [{"role": "user", "content": "请回复“连接成功”。"}],
        "temperature": 0,
        "stream": False,
    }


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={}),
        httpx.Response(200, text="not-json"),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": [{"message": {"content": ""}}]}),
        httpx.Response(200, json={"choices": [{"message": {}}]}),
    ],
)
@respx.mock
async def test_model_connection_rejects_empty_or_malformed_success_response(
    config: ModelConfig, response: httpx.Response
) -> None:
    respx.post("https://example.test/v1/chat/completions").mock(return_value=response)

    with pytest.raises(DomainError) as error:
        await OpenAICompatibleProvider(config, "test-key").test_connection()

    assert error.value.code == "MODEL_PROTOCOL_ERROR"
    assert error.value.retryable is False
    assert error.value.message == "模型服务返回了无法识别的数据"
