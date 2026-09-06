import pytest
from app.api.errors import DomainError
from app.core.llm import ChatDelta, ChatRequest
from app.core.model_router import ModelRouter

class Fake:
    def __init__(self, values=(), error=None): self.values, self.error, self.calls = list(values), error, 0
    async def test_connection(self): return None
    async def stream_chat(self, request):
        self.calls += 1
        if self.error: raise self.error
        for value in self.values: yield ChatDelta(content=value)

@pytest.mark.asyncio
async def test_cloud_only_never_calls_local():
    local, cloud = Fake(["l"]), Fake(["c"])
    route = ModelRouter("cloud_only", local, cloud, "m", "c")
    result = await route.open_stream(ChatRequest(messages=[]))
    assert result.route.source == "cloud" and local.calls == 0

@pytest.mark.asyncio
async def test_automatic_falls_back_before_output():
    local = Fake(error=DomainError("OLLAMA_UNAVAILABLE", "x", 503, True)); cloud = Fake(["c"])
    result = await ModelRouter("automatic", local, cloud, "m", "c").open_stream(ChatRequest(messages=[]))
    assert result.route.fallback_reason == "OLLAMA_UNAVAILABLE"
