from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from app.api.errors import DomainError
from app.core.llm import ChatRequest, LLMMessage
from app.core.local_model import LocalModelProvider
from app.schemas.local_model import LocalModelConfig

BASE = "http://127.0.0.1:1234"


def _config(model: str = "qwen2.5:0.5b") -> LocalModelConfig:
    return LocalModelConfig(base_url=BASE, model=model)


async def _drain(request: ChatRequest) -> list[str]:
    provider = LocalModelProvider(config=_config())
    return [delta.content async for delta in provider.stream_chat(request)]


def _sse(chunks: list[str]) -> str:
    return "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)


def _chunk(content: str) -> dict[str, Any]:
    return {"choices": [{"delta": {"content": content}}]}


# ---------------------------------------------------------------- discovery


@respx.mock
async def test_lists_models_from_openai_compatible_endpoint():
    route = respx.get(f"{BASE}/v1/models").mock(
        return_value=httpx.Response(200, json={"object": "list", "data": [{"id": "qwen2.5:0.5b"}]})
    )
    assert await LocalModelProvider(config=_config()).list_models() == ["qwen2.5:0.5b"]
    assert route.called


@respx.mock
async def test_empty_null_data_means_no_models_not_a_protocol_error():
    """A local server with nothing loaded answers `data: null`.

    Treating that as a protocol error is what made a fresh install look broken.
    """
    respx.get(f"{BASE}/v1/models").mock(
        return_value=httpx.Response(200, json={"object": "list", "data": None})
    )
    assert await LocalModelProvider(config=_config()).list_models() == []


@respx.mock
async def test_empty_list_data_also_means_no_models():
    respx.get(f"{BASE}/v1/models").mock(
        return_value=httpx.Response(200, json={"object": "list", "data": []})
    )
    assert await LocalModelProvider(config=_config()).list_models() == []


@respx.mock
async def test_non_list_data_is_still_a_protocol_error():
    """`None` is special-cased; a wrong *type* is genuinely malformed."""
    respx.get(f"{BASE}/v1/models").mock(
        return_value=httpx.Response(200, json={"object": "list", "data": "nope"})
    )
    with pytest.raises(DomainError) as error:
        await LocalModelProvider(config=_config()).list_models()
    assert error.value.code == "LOCAL_MODEL_PROTOCOL_ERROR"


@respx.mock
async def test_missing_models_endpoint_soft_fails():
    respx.get(f"{BASE}/v1/models").mock(return_value=httpx.Response(404))
    assert await LocalModelProvider(config=_config()).list_models() == []


@respx.mock
async def test_transport_failure_reports_unavailable():
    respx.get(f"{BASE}/v1/models").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(DomainError) as error:
        await LocalModelProvider(config=_config()).list_models()
    assert error.value.code == "LOCAL_MODEL_UNAVAILABLE"


# ---------------------------------------------------------------- preflight


@respx.mock
async def test_preflight_rejects_a_model_the_server_does_not_offer():
    respx.get(f"{BASE}/v1/models").mock(
        return_value=httpx.Response(200, json={"object": "list", "data": [{"id": "other"}]})
    )
    with pytest.raises(DomainError) as error:
        await LocalModelProvider(config=_config()).preflight_model("qwen2.5:0.5b")
    assert error.value.code == "LOCAL_MODEL_NOT_FOUND"


@respx.mock
async def test_preflight_accepts_a_model_the_server_offers():
    respx.get(f"{BASE}/v1/models").mock(
        return_value=httpx.Response(200, json={"object": "list", "data": [{"id": "qwen2.5:0.5b"}]})
    )
    await LocalModelProvider(config=_config()).preflight_model("qwen2.5:0.5b")


# ---------------------------------------------------------------- streaming


@respx.mock
async def test_streams_over_openai_compatible_sse():
    """The native NDJSON dialect is gone; the shared SSE reader is used instead."""
    respx.post(f"{BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse([_chunk("Hi"), _chunk(" there"), {"choices": []}]),
        )
    )
    chunks = await _drain(ChatRequest(messages=[LLMMessage(role="user", content="hi")]))
    assert chunks == ["Hi", " there"]


@respx.mock
async def test_sends_standard_openai_payload_to_any_port():
    route = respx.post(f"{BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse([_chunk("ok")]),
        )
    )
    await _drain(ChatRequest(messages=[LLMMessage(role="user", content="hi")]))
    sent = route.calls[0].request
    assert json.loads(sent.content) == {
        "model": "qwen2.5:0.5b",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
        "temperature": 0.2,
    }


@respx.mock
async def test_no_authorization_header_when_no_key_configured():
    route = respx.post(f"{BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse([_chunk("ok")]),
        )
    )
    provider = LocalModelProvider(config=_config())
    async for _ in provider.stream_chat(ChatRequest(messages=[LLMMessage(role="user", content="hi")])):
        pass
    assert route.calls[0].request.headers.get("authorization") is None


@respx.mock
async def test_authorization_header_present_when_key_configured():
    route = respx.post(f"{BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse([_chunk("ok")]),
        )
    )
    config = _config().model_copy(update={"api_key": "secret"})
    provider = LocalModelProvider(config=config)
    async for _ in provider.stream_chat(ChatRequest(messages=[LLMMessage(role="user", content="hi")])):
        pass
    assert route.calls[0].request.headers["authorization"] == "Bearer secret"


@respx.mock
async def test_no_reasoning_field_is_sent_for_local_models():
    """Local ids are user-chosen, so no vendor reasoning field can be assumed."""
    route = respx.post(f"{BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse([_chunk("ok")]),
        )
    )
    await _drain(
        ChatRequest(messages=[LLMMessage(role="user", content="hi")], reasoning_effort="high")
    )
    sent = json.loads(route.calls[0].request.content)
    assert "reasoning_effort" not in sent
    assert "thinking" not in sent


@respx.mock
async def test_stream_without_any_text_delta_is_a_protocol_error():
    respx.post(f"{BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse([{"choices": [{"delta": {}}]}]),
        )
    )
    with pytest.raises(DomainError) as error:
        await _drain(ChatRequest(messages=[LLMMessage(role="user", content="hi")]))
    assert error.value.code == "LOCAL_MODEL_PROTOCOL_ERROR"


@respx.mock
async def test_non_sse_content_type_is_a_protocol_error():
    respx.post(f"{BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(200, headers={"content-type": "application/json"}, content="{}")
    )
    with pytest.raises(DomainError) as error:
        await _drain(ChatRequest(messages=[LLMMessage(role="user", content="hi")]))
    assert error.value.code == "LOCAL_MODEL_PROTOCOL_ERROR"


@respx.mock
async def test_missing_model_maps_to_local_not_found():
    respx.post(f"{BASE}/v1/chat/completions").mock(return_value=httpx.Response(404))
    with pytest.raises(DomainError) as error:
        await _drain(ChatRequest(messages=[LLMMessage(role="user", content="hi")]))
    assert error.value.code == "LOCAL_MODEL_NOT_FOUND"


@respx.mock
async def test_transport_failure_during_stream_reports_unavailable():
    respx.post(f"{BASE}/v1/chat/completions").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(DomainError) as error:
        await _drain(ChatRequest(messages=[LLMMessage(role="user", content="hi")]))
    assert error.value.code == "LOCAL_MODEL_UNAVAILABLE"


@respx.mock
async def test_test_connection_probes_v1_models():
    respx.get(f"{BASE}/v1/models").mock(
        return_value=httpx.Response(200, json={"object": "list", "data": None})
    )
    result = await LocalModelProvider(config=_config()).test_connection()
    assert result.connected is True


# -------------------------------------------------------- loopback enforcement


@respx.mock
async def test_resolver_rejects_non_loopback_answer_before_any_http_call():
    class RebindingResolver:
        async def resolve(self, host):
            return ["93.184.216.34"]

    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    provider = LocalModelProvider(
        config=LocalModelConfig(base_url="http://localhost:1234", model="m"),
        transport=transport,
        resolver=RebindingResolver(),
    )
    with pytest.raises(DomainError) as error:
        await provider.list_models()
    assert error.value.code == "LOCAL_MODEL_UNAVAILABLE"
