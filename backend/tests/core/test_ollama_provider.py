import httpx
import pytest

from app.core.llm import ChatRequest, LLMMessage
from app.core.ollama import OllamaProvider
from app.core.ollama import PullCoordinator


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
