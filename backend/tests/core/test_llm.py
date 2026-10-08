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
    LLMToolCall,
    LLMToolSpec,
    ModelConfig,
    OpenAICompatibleProvider,
    ToolCallAccumulator,
)
from app.core.model_capabilities import accepts_temperature
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


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"data": []}),
        httpx.Response(200, json={"object": "list", "data": None}),
    ],
)
async def test_list_models_treats_empty_catalogue_as_no_models(
    config: ModelConfig, response: httpx.Response
) -> None:
    """An empty catalogue is a state, not a fault.

    A local server with nothing loaded answers `data: null`; reporting that as
    a protocol error made a fresh install look broken.
    """
    respx.get("https://example.test/v1/models").mock(return_value=response)

    assert await OpenAICompatibleProvider(config, "test-key").list_models() == []


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
    ("preset", "model", "effort", "expected_extra"),
    [
        # Zhipu and Xiaomi drive reasoning through thinking.type, whose two
        # states are exposed as off/on — not as an intensity scale.
        ("glm", "glm-4.6", "on", {"thinking": {"type": "enabled"}}),
        ("mimo", "mimo-v2.6-pro", "on", {"thinking": {"type": "enabled"}}),
        # DeepSeek: "off" is thinking.type, but an intensity is reasoning_effort.
        ("deepseek", "deepseek-flash", "off", {"thinking": {"type": "disabled"}}),
        ("deepseek", "deepseek-flash", "max", {"reasoning_effort": "max"}),
        # Kimi K3 is the reasoning_effort model, and cannot be disabled.
        ("kimi", "kimi-k3", "high", {"reasoning_effort": "high"}),
        # Kimi K2.6 is a thinking.type model — same vendor, different field.
        ("kimi", "kimi-k2.6", "on", {"thinking": {"type": "enabled"}}),
        # OpenAI takes the vendor's own value verbatim, including none/xhigh.
        ("openai", "gpt-6-astra", "xhigh", {"reasoning_effort": "xhigh"}),
        ("openai", "gpt-5.6-terra", "none", {"reasoning_effort": "none"}),
        # Aggregated gateways get nothing extra.
        ("opencode_zen", "mimo-v2.5-free", "high", {}),
        ("opencode_go", "mimo-v2.5", "high", {}),
    ],
)
async def test_stream_sends_model_specific_reasoning_fields(
    config: ModelConfig,
    chat_request: ChatRequest,
    preset: str,
    model: str,
    effort: str,
    expected_extra: dict,
) -> None:
    route = _stream_route()
    configured = config.model_copy(
        update={"preset": preset, "model": model, "reasoning_effort": effort}
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
@pytest.mark.parametrize(
    ("preset", "model"),
    [
        # gpt-5.5: "Unsupported parameter: 'temperature'". The 5.6 family
        # accepts only the default 1, and GPT-6 Astra requires omitting it.
        ("openai", "gpt-5.5"),
        ("openai", "gpt-5.6-terra"),
        ("openai", "gpt-6-astra"),
        # Kimi K2.x fixes temperature at 1.0 and errors on anything else.
        ("kimi", "kimi-k3"),
    ],
)
async def test_stream_omits_temperature_for_models_that_reject_it(
    config: ModelConfig,
    chat_request: ChatRequest,
    preset: str,
    model: str,
) -> None:
    route = _stream_route()
    configured = config.model_copy(update={"preset": preset, "model": model})

    _ = [
        delta
        async for delta in OpenAICompatibleProvider(configured, "test-key").stream_chat(chat_request)
    ]

    sent = json.loads(route.calls[0].request.content)
    assert "temperature" not in sent


@respx.mock
async def test_stream_still_sends_temperature_for_models_that_accept_it(
    config: ModelConfig,
    chat_request: ChatRequest,
) -> None:
    route = _stream_route()
    configured = config.model_copy(update={"preset": "openai", "model": "gpt-5.4"})

    _ = [
        delta
        async for delta in OpenAICompatibleProvider(configured, "test-key").stream_chat(chat_request)
    ]

    sent = json.loads(route.calls[0].request.content)
    assert sent["temperature"] == pytest.approx(chat_request.temperature)


@respx.mock
async def test_every_vendor_request_carries_only_fields_that_model_accepts(
    config: ModelConfig,
) -> None:
    """端到端核对每个厂商的最终请求体。

    这是本次两处修复的判据合集：档位必须是厂商原生值（K3 的 max、
    GLM-5.2 的 xhigh、GPT-5.5 的 none 都要原样透传），temperature 必须在
    拒绝它的模型上整段消失，且两种推理字段永不同时下发。
    """
    cases = [
        # (preset, model, effort, 期望出现在请求体里的额外字段)
        ("kimi", "kimi-k3", "max", {"reasoning_effort": "max"}),
        ("kimi", "kimi-k2.6", "on", {"thinking": {"type": "enabled"}}),
        ("kimi", "kimi-k2.6", "off", {"thinking": {"type": "disabled"}}),
        ("glm", "glm-5.3", "max", {"reasoning_effort": "max"}),
        ("glm", "glm-5.2", "xhigh", {"reasoning_effort": "xhigh"}),
        ("glm", "glm-5.2", "none", {"reasoning_effort": "none"}),
        ("glm", "glm-4.6", "off", {"thinking": {"type": "disabled"}}),
        ("deepseek", "deepseek-flash", "off", {"thinking": {"type": "disabled"}}),
        ("deepseek", "deepseek-flash", "max", {"reasoning_effort": "max"}),
        ("openai", "gpt-5.6-terra", "xhigh", {"reasoning_effort": "xhigh"}),
        ("openai", "gpt-5.5", "none", {"reasoning_effort": "none"}),
        ("openai", "gpt-6-astra", "high", {"reasoning_effort": "high"}),
        # Non-reasoning model: no vendor field at all.
        ("openai", "gpt-4.1", "", {}),
        ("mimo", "mimo-v2.6-pro", "off", {"thinking": {"type": "disabled"}}),
        # Unverified vendor: conservative, nothing but the OpenAI basics.
        ("qwen", "qwen3.8-max", "high", {}),
    ]
    route = _stream_route()
    base = ChatRequest(messages=[LLMMessage(role="user", content="hi")], temperature=0.3)

    for preset, model, effort, expected in cases:
        before = route.call_count
        configured = config.model_copy(
            update={"preset": preset, "model": model, "reasoning_effort": effort}
        )
        _ = [
            delta
            async for delta in OpenAICompatibleProvider(configured, "test-key").stream_chat(base)
        ]
        assert route.call_count == before + 1, f"{preset}/{model} 的请求没有命中 mock"
        sent = json.loads(route.calls[before].request.content)
        # `temperature` is a legitimate field where the model accepts it, so the
        # reasoning shape is compared separately from the sampling field.
        has_temperature = "temperature" in sent
        extras = {k: v for k, v in sent.items() if k not in ("model", "messages", "stream")}
        extras.pop("temperature", None)
        assert extras == expected, f"{preset}/{model} 档位={effort!r} 实际下发 {extras}"
        assert has_temperature is accepts_temperature(preset, model), (
            f"{preset}/{model} 的 temperature 应当"
            f"{'发送' if accepts_temperature(preset, model) else '省略'}"
        )
        # Never both reasoning shapes at once.
        assert not ("reasoning_effort" in sent and "thinking" in sent)


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


# ------------------------------------------------------------------ 工具调用
# 本地声明一个工具规格，避免 tests/core 反向依赖 app.chat。
_EchoTool = LLMToolSpec(
    name="web_search",
    description="搜索",
    parameters={"type": "object", "properties": {"query": {"type": "string"}}},
)


@respx.mock
async def test_stream_reports_tool_call_fragments_instead_of_a_protocol_error(
    config: ModelConfig,
) -> None:
    """只有 tool_calls 的流必须被接受，且分片要能拼回完整调用。

    旧解析器只读 `delta.content`，于是这种帧全被判成"无内容"跳过，最后一帧都没有
    就抛 MODEL_PROTOCOL_ERROR —— 一个完全正常的工具调用被报成模型协议错误。
    """
    respx.post("https://example.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_a",'
                '"type":"function","function":{"name":"web_search",'
                '"arguments":"{\\"query\\":"}}]}}]}\n\n'
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
                '"function":{"arguments":"\\"RAG\\"}"}}]}}]}\n\n'
                "data: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )
    )

    deltas = [
        delta
        async for delta in OpenAICompatibleProvider(config, "test-key").stream_chat(
            ChatRequest(messages=[LLMMessage(role="user", content="q")], tools=[_EchoTool])
        )
    ]

    assert [delta.content for delta in deltas] == ["", ""]
    accumulator = ToolCallAccumulator()
    for delta in deltas:
        accumulator.add(delta.tool_calls)
    assert accumulator.complete() == [
        LLMToolCall(id="call_a", name="web_search", arguments='{"query":"RAG"}')
    ]


@respx.mock
async def test_stream_sends_tools_with_snake_case_message_fields(config: ModelConfig) -> None:
    """🔴 契约回归：`tool_call_id` 绝不能变成 `toolCallId`。

    `LLMMessage` 一旦退回 `WireModel`（`serialize_by_alias=True`），下划线字段就会被
    转成 camelCase，OpenAI 兼容接口只会回一个 400，而且从错误里看不出是别名问题。
    """
    route = respx.post("https://example.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n',
            headers={"content-type": "text/event-stream"},
        )
    )
    request = ChatRequest(
        messages=[
            LLMMessage(
                role="assistant",
                tool_calls=[LLMToolCall(id="call_a", name="web_search", arguments="{}")],
            ),
            LLMMessage(role="tool", tool_call_id="call_a", content="结果"),
        ],
        tools=[_EchoTool],
    )

    [delta async for delta in OpenAICompatibleProvider(config, "test-key").stream_chat(request)]

    payload = json.loads(route.calls[0].request.content)
    assert payload["tools"] == [_EchoTool.to_wire()]
    assert payload["messages"][0]["tool_calls"][0]["function"]["name"] == "web_search"
    assert payload["messages"][1]["tool_call_id"] == "call_a"
    assert "toolCall" not in json.dumps(payload)


@respx.mock
async def test_stream_retries_without_tools_when_the_model_rejects_them(
    config: ModelConfig,
) -> None:
    """未登记但实际拒绝 `tools` 的模型要降级成普通回答，而不是整条请求失败。"""
    route = respx.post("https://example.test/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(400, json={"error": {"message": "unsupported parameter"}}),
            httpx.Response(
                200,
                text='data: {"choices":[{"delta":{"content":"降级成功"}}]}\n\ndata: [DONE]\n\n',
                headers={"content-type": "text/event-stream"},
            ),
        ]
    )

    deltas = [
        delta
        async for delta in OpenAICompatibleProvider(config, "test-key").stream_chat(
            ChatRequest(messages=[LLMMessage(role="user", content="q")], tools=[_EchoTool])
        )
    ]

    assert [delta.content for delta in deltas] == ["降级成功"]
    assert len(route.calls) == 2
    assert "tools" in json.loads(route.calls[0].request.content)
    assert "tools" not in json.loads(route.calls[1].request.content)


@respx.mock
async def test_stream_does_not_retry_a_rejection_when_no_tools_were_sent(
    config: ModelConfig,
) -> None:
    """没有发 tools 时的 400 是真错误，重试只会白跑一趟。"""
    route = respx.post("https://example.test/v1/chat/completions").mock(
        return_value=httpx.Response(400, json={"error": {"message": "bad model"}})
    )

    with pytest.raises(DomainError) as error:
        [
            delta
            async for delta in OpenAICompatibleProvider(config, "test-key").stream_chat(
                ChatRequest(messages=[LLMMessage(role="user", content="q")])
            )
        ]

    assert error.value.code == "MODEL_PROTOCOL_ERROR"
    assert len(route.calls) == 1


@respx.mock
async def test_stream_does_not_retry_after_output_was_already_emitted(
    config: ModelConfig,
) -> None:
    """已经吐出内容后再失败不能重试 —— 否则用户会看到同一段回答出现两次。"""
    route = respx.post("https://example.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"前半段"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":123}}]}\n\n'
                "data: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )
    )

    received: list[str] = []
    with pytest.raises(DomainError) as error:
        async for delta in OpenAICompatibleProvider(config, "test-key").stream_chat(
            ChatRequest(messages=[LLMMessage(role="user", content="q")], tools=[_EchoTool])
        ):
            received.append(delta.content)

    assert received == ["前半段"]
    assert error.value.code == "MODEL_PROTOCOL_ERROR"
    assert len(route.calls) == 1
