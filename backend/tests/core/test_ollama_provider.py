import json

import httpx
import pytest

from app.core.llm import ChatRequest, LLMMessage
from app.core.ollama import OllamaProvider, PullCoordinator
from app.schemas.ollama import OllamaConfig


class FakeResolver:
    def __init__(self, answers): self.answers = answers
    async def resolve(self, host): return self.answers


@pytest.mark.asyncio
async def test_streams_ndjson_content_and_done():
    def handler(request):
        return httpx.Response(200, content=b'{"message":{"content":"Hi"}}\n{"done":true}\n')
    provider = OllamaProvider("http://127.0.0.1:11434", "qwen2.5:7b", transport=httpx.MockTransport(handler))
    result = [d async for d in provider.stream_chat(ChatRequest(messages=[LLMMessage(role="user", content="x")]))]
    assert [d.content for d in result] == ["Hi"]


@pytest.mark.asyncio
async def test_protocol_error_on_error_line():
    def handler(request):
        return httpx.Response(200, content=b'{"error":"bad"}\n')
    provider = OllamaProvider("http://127.0.0.1:11434", "m", transport=httpx.MockTransport(handler))
    with pytest.raises(Exception) as exc:
        [d async for d in provider.stream_chat(ChatRequest(messages=[]))]
    assert getattr(exc.value, "code", None) == "OLLAMA_PROTOCOL_ERROR"

@pytest.mark.asyncio
async def test_pull_coordinator_projects_progress_without_raw_payload():
    coordinator = PullCoordinator("http://127.0.0.1:11434", transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b'{"status":"downloading","completed":5,"total":10}\n{"status":"success","done":true}\n')))
    events = [event async for event in coordinator.pull("m")]
    assert events[0]["progress"] == 50
    assert "completed" not in events[0]

@pytest.mark.asyncio
async def test_provider_rejects_non_loopback_dns_answer_before_http():
    provider = OllamaProvider("http://localhost:11434", "m", resolver=FakeResolver(["192.168.1.4"]), transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b'{"message":{"content":"x"}}\n{"done":true}\n')))
    with pytest.raises(Exception) as exc:
        [d async for d in provider.stream_chat(ChatRequest(messages=[]))]
    assert getattr(exc.value, "code", None) == "OLLAMA_UNAVAILABLE"


@pytest.mark.asyncio
async def test_provider_accepts_frozen_config_constructor_and_sends_contract_payload():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=b'{"message":{"content":"ok"}}\n{"done":true}\n')

    provider = OllamaProvider(
        OllamaConfig(model="qwen2.5:7b", timeout_seconds=42),
        transport=httpx.MockTransport(handler),
    )
    result = [d async for d in provider.stream_chat(ChatRequest(messages=[LLMMessage(role="user", content="hello")]))]
    assert [item.content for item in result] == ["ok"]
    assert seen[0].url.path == "/api/chat"
    assert seen[0].headers.get("authorization") is None
    assert json.loads(seen[0].content) == {
        "model": "qwen2.5:7b",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
        "options": {"temperature": 0.2},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        b'{"done":true}\n{"message":{"content":"late"}}\n',
        b'{"message":{"content":42}}\n{"done":true}\n',
        b'{"message":{}}\n',
    ],
)
async def test_provider_rejects_invalid_terminal_or_content_shape(body):
    provider = OllamaProvider(
        OllamaConfig(model="m"),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body)),
    )
    with pytest.raises(Exception) as raised:
        [item async for item in provider.stream_chat(ChatRequest(messages=[]))]
    assert getattr(raised.value, "code", None) == "OLLAMA_PROTOCOL_ERROR"
