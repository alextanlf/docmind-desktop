import pytest

from app.core.llm import ChatDelta, ChatRequest
from app.core.model_router import ModelRouter


@pytest.mark.asyncio
async def test_cloud_only_router_streams_cloud_without_local_call():
    class Fake:
        def __init__(self): self.calls = 0
        async def stream_chat(self, request):
            self.calls += 1
            yield ChatDelta(content="cloud")
    local, cloud = Fake(), Fake()
    routed = await ModelRouter("cloud_only", local, cloud, "local", "cloud").open_stream(ChatRequest(messages=[]))
    assert [d.content async for d in routed.deltas] == ["cloud"]
    assert local.calls == 0 and cloud.calls == 1
