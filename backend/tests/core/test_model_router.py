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

class PartialFailure(Fake):
    async def stream_chat(self, request):
        self.calls += 1
        yield ChatDelta(content="local")
        raise DomainError("OLLAMA_UNAVAILABLE", "x", 503, True)

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

@pytest.mark.asyncio
async def test_automatic_ignores_empty_delta_before_first_non_empty_output():
    local, cloud = Fake(["", "local"]), Fake(["cloud"])
    result = await ModelRouter("automatic", local, cloud, "m", "c").open_stream(ChatRequest(messages=[]))
    assert result.route.source == "local"
    assert [delta.content async for delta in result.deltas] == ["local"]

@pytest.mark.asyncio
async def test_automatic_does_not_switch_after_first_non_empty_delta():
    local, cloud = PartialFailure(), Fake(["cloud"])
    result = await ModelRouter("automatic", local, cloud, "m", "c").open_stream(ChatRequest(messages=[]))
    assert result.route.source == "local"
    with pytest.raises(DomainError):
        [delta async for delta in result.deltas]
    assert cloud.calls == 0
