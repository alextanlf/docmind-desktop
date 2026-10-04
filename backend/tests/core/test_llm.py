from __future__ import annotations

import json
import logging

import httpx
import pytest
import respx

from app.api.errors import DomainError
from app.core.llm import (
    CONNECTION_TEST_TEMPERATURE,
    ChatRequest,
    LLMMessage,
    ModelConfig,
    OpenAICompatibleProvider,
)
from app.schemas.settings import MODEL_PRESETS


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


def _frame(delta: dict[str, object]) -> str:
    return "data: " + json.dumps({"choices": [{"delta": delta}]}) + "\n\n"


# Reasoning vendors (GLM thinking, Kimi, MiMo) interleave frames that carry no
# text delta, and some close the stream without the `[DONE]` sentinel. These
# were verified against the live endpoints before being relaxed.
@respx.mock
async def test_stream_skips_reasoning_and_usage_frames_from_reasoning_models(
    config: ModelConfig, chat_request: ChatRequest
) -> None:
    body = (
        _frame({"role": "assistant", "content": ""})
        + _frame({"reasoning_content": "用户要求回复“连接成功”"})
        + 'data: {"choices":[],"usage":{"total_tokens":9}}\n\n'
        + _frame({"content": "连接"})
        + _frame({"content": "成功"})
        + 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        + "data: [DONE]\n\n"
    )
    respx.post("https://example.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
    )

    deltas = [delta async for delta in OpenAICompatibleProvider(config, "test-key").stream_chat(chat_request)]

    assert [delta.content for delta in deltas] == ["连接", "成功"]


@respx.mock
async def test_stream_accepts_truncated_body_without_done_sentinel(
    config: ModelConfig, chat_request: ChatRequest
) -> None:
    """Vendors may close the connection without a trailing blank line or `[DONE]`."""
    body = _frame({"content": "连接成功"}) + 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
    respx.post("https://example.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
    )

    deltas = [delta async for delta in OpenAICompatibleProvider(config, "test-key").stream_chat(chat_request)]

    assert [delta.content for delta in deltas] == ["连接成功"]


@respx.mock
async def test_stream_rejects_non_string_content(
    config: ModelConfig, chat_request: ChatRequest
) -> None:
    """A delta whose `content` is a number is corrupt data, not a skippable frame."""
    body = _frame({"content": 42}) + "data: [DONE]\n\n"
    respx.post("https://example.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
    )

    with pytest.raises(DomainError) as error:
        _ = [delta async for delta in OpenAICompatibleProvider(config, "test-key").stream_chat(chat_request)]

    assert error.value.code == "MODEL_PROTOCOL_ERROR"


@respx.mock
async def test_stream_fails_when_only_reasoning_content_is_emitted(
    config: ModelConfig, chat_request: ChatRequest
) -> None:
    """Reasoning-only output is still an empty answer, so it must not look successful."""
    body = _frame({"reasoning_content": "思考中"}) + "data: [DONE]\n\n"
    respx.post("https://example.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
    )

    with pytest.raises(DomainError) as error:
        _ = [delta async for delta in OpenAICompatibleProvider(config, "test-key").stream_chat(chat_request)]

    assert error.value.code == "MODEL_PROTOCOL_ERROR"


@pytest.mark.parametrize("preset", ["kimi", "glm", "mimo"])
def test_new_vendor_presets_are_registered(preset: str) -> None:
    base_url, model = MODEL_PRESETS[preset]
    assert base_url.startswith("https://")
    assert model
    # Preset base URLs must survive the ModelConfig normalizer unchanged.
    assert ModelConfig(preset=preset, base_url=base_url, model=model, timeout_seconds=30).base_url == base_url


@respx.mock
async def test_list_models_reads_openai_compatible_models_endpoint(
    config: ModelConfig,
) -> None:
    route = respx.get("https://example.test/v1/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"id": "kimi-k2.5"},
                    {"id": "glm-4.6"},
                    {"id": "  "},
                    {"id": 7},
                    "not-a-dict",
                ]
            },
        )
    )

    models = await OpenAICompatibleProvider(config, "test-key").list_models()

    assert [model.id for model in models] == ["kimi-k2.5", "glm-4.6"]
    assert models[0].label == "Kimi K2.5"
    assert dict(route.calls[0].request.headers)["authorization"] == "Bearer test-key"


@respx.mock
async def test_list_models_returns_empty_when_provider_has_no_models_endpoint(
    config: ModelConfig,
) -> None:
    respx.get("https://example.test/v1/models").mock(return_value=httpx.Response(404))

    assert await OpenAICompatibleProvider(config, "test-key").list_models() == []


@respx.mock
async def test_list_models_maps_auth_failure(config: ModelConfig) -> None:
    respx.get("https://example.test/v1/models").mock(return_value=httpx.Response(401))

    with pytest.raises(DomainError) as error:
        await OpenAICompatibleProvider(config, "test-key").list_models()

    assert error.value.code == "MODEL_AUTH_FAILED"


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={}),
        httpx.Response(200, json={"data": []}),
        httpx.Response(200, json={"data": "not-a-list"}),
        httpx.Response(200, text="not-json"),
    ],
)
async def test_list_models_rejects_malformed_payload(
    config: ModelConfig, response: httpx.Response
) -> None:
    respx.get("https://example.test/v1/models").mock(return_value=response)

    with pytest.raises(DomainError) as error:
        await OpenAICompatibleProvider(config, "test-key").list_models()

    assert error.value.code == "MODEL_PROTOCOL_ERROR"


async def test_list_models_skips_request_when_base_url_is_empty() -> None:
    empty = ModelConfig(preset="custom", base_url="", model="", timeout_seconds=12)

    assert await OpenAICompatibleProvider(empty, "test-key").list_models() == []


def _stream_route() -> respx.Route:
    return respx.post("https://example.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"好"}}]}\n\ndata: [DONE]\n\n',
            headers={"content-type": "text/event-stream"},
        )
    )


@respx.mock
@pytest.mark.parametrize(
    ("preset", "model", "expected_extra"),
    [
        # Zhipu and Xiaomi drive reasoning through thinking.type.
        ("glm", "glm-4.6", {"thinking": {"type": "enabled"}}),
        ("mimo", "mimo-v2.6-pro", {"thinking": {"type": "enabled"}}),
        # DeepSeek's toggle is also thinking.type, not reasoning_effort.
        ("deepseek", "deepseek-flash", {"thinking": {"type": "enabled"}}),
        # Kimi K3 is the reasoning_effort model, and cannot be disabled.
        ("kimi", "kimi-k3", {"reasoning_effort": "high"}),
        # Kimi K2.6 is a thinking.type model — same vendor, different field.
        ("kimi", "kimi-k2.6", {"thinking": {"type": "enabled"}}),
        # Aggregated gateways get nothing extra.
        ("opencode_zen", "mimo-v2.5-free", {}),
        ("opencode_go", "mimo-v2.5", {}),
    ],
)
async def test_stream_sends_model_specific_reasoning_fields(
    config: ModelConfig,
    chat_request: ChatRequest,
    preset: str,
    model: str,
    expected_extra: dict,
) -> None:
    route = _stream_route()
    configured = config.model_copy(
        update={"preset": preset, "model": model, "reasoning_effort": "high"}
    )

    _ = [
        delta
        async for delta in OpenAICompatibleProvider(configured, "test-key").stream_chat(chat_request)
    ]

    sent = json.loads(route.calls[0].request.content)
    for key, value in expected_extra.items():
        assert sent[key] == value
    # Never both shapes at once: that combination is rejected by some vendors.
    assert not ("reasoning_effort" in sent and "thinking" in sent)
    if not expected_extra:
        assert "reasoning_effort" not in sent and "thinking" not in sent


@respx.mock
async def test_stream_clamps_zero_temperature_to_a_vendor_safe_value(
    config: ModelConfig,
) -> None:
    """query-style tasks ask for temperature 0, which Zhipu rejects outright."""
    route = _stream_route()

    _ = [
        delta
        async for delta in OpenAICompatibleProvider(config, "test-key").stream_chat(
            ChatRequest(messages=[LLMMessage(role="user", content="改写")], temperature=0)
        )
    ]

    assert json.loads(route.calls[0].request.content)["temperature"] == 0.1


@respx.mock
async def test_stream_does_not_send_reasoning_fields_when_effort_is_unset(
    config: ModelConfig, chat_request: ChatRequest
) -> None:
    """No configured level means "vendor default", not an invented field."""
    route = _stream_route()
    configured = config.model_copy(update={"preset": "glm", "model": "glm-4.6", "reasoning_effort": ""})

    _ = [
        delta
        async for delta in OpenAICompatibleProvider(configured, "test-key").stream_chat(chat_request)
    ]

    sent = json.loads(route.calls[0].request.content)
    assert "thinking" not in sent and "reasoning_effort" not in sent


@respx.mock
async def test_request_level_effort_overrides_configured_default(
    config: ModelConfig, chat_request: ChatRequest
) -> None:
    route = _stream_route()
    configured = config.model_copy(update={"preset": "kimi", "model": "kimi-k3", "reasoning_effort": "low"})

    _ = [
        delta
        async for delta in OpenAICompatibleProvider(configured, "test-key").stream_chat(
            chat_request.model_copy(update={"reasoning_effort": "high"})
        )
    ]

    assert json.loads(route.calls[0].request.content)["reasoning_effort"] == "high"


@respx.mock
async def test_connection_probe_never_sends_vendor_specific_fields(
    config: ModelConfig,
) -> None:
    """The connectivity check must not depend on optional parameters."""
    route = respx.post("https://example.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "连接成功"}}]})
    )
    configured = config.model_copy(update={"preset": "glm", "model": "glm-4.6", "reasoning_effort": "high"})

    await OpenAICompatibleProvider(configured, "test-key").test_connection()

    sent = json.loads(route.calls[0].request.content)
    assert "thinking" not in sent and "reasoning_effort" not in sent


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
    # Zhipu documents temperature as (0, 1) and rejects 0, so the probe cannot use
    # absolute zero even though DeepSeek and Qwen accept it.
    assert json.loads(route.calls[0].request.content) == {
        "model": "example-model",
        "messages": [{"role": "user", "content": "请回复“连接成功”。"}],
        "temperature": CONNECTION_TEST_TEMPERATURE,
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
