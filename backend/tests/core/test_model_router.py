import pytest

from app.api.errors import DomainError
from app.core.llm import ChatDelta, ChatRequest
from app.core.model_router import ModelRouter
from app.schemas.ollama import OllamaConfig, RoutingSettings, RuntimeSettingsInput


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


def runtime(mode: str, model: str = "local"):
    return RuntimeSettingsInput(
        ollama=OllamaConfig(model=model), routing=RoutingSettings(mode=mode)
    )

@pytest.mark.asyncio
async def test_cloud_only_never_calls_local():
    local, cloud = Fake(["l"]), Fake(["c"])
    route = ModelRouter("cloud_only", local, cloud, "m", "c")
    result = await route.open_stream(ChatRequest(messages=[]))
    assert result.route.source == "cloud" and local.calls == 0

@pytest.mark.asyncio
async def test_local_only_rejects_missing_local_model_before_provider_call():
    local, cloud = Fake(["l"]), Fake(["c"])
    with pytest.raises(DomainError) as exc:
        await ModelRouter("local_only", local, cloud, "", "c").open_stream(ChatRequest(messages=[]))
    assert exc.value.code == "LOCAL_MODEL_UNAVAILABLE" and local.calls == 0

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


@pytest.mark.asyncio
async def test_runtime_constructor_runs_local_preflight_before_stream():
    class Local(Fake):
        async def test_connection(self):
            self.preflight_calls = getattr(self, "preflight_calls", 0) + 1

    local, cloud = Local(["local"]), Fake(["cloud"])
    router = ModelRouter(runtime=runtime("local_only"), local=local, cloud=cloud)
    routed = await router.open_stream(ChatRequest(messages=[]))
    assert routed.route.source == "local"
    assert local.preflight_calls == 1
    assert [item.content async for item in routed.deltas] == ["local"]
    assert cloud.calls == 0


@pytest.mark.asyncio
async def test_automatic_maps_cloud_secondary_failure_to_routing_error():
    local = Fake(error=DomainError("OLLAMA_UNAVAILABLE", "offline", 503, True))
    cloud = Fake(error=DomainError("MODEL_UNAVAILABLE", "cloud down", 503, True))
    router = ModelRouter(runtime=runtime("automatic"), local=local, cloud=cloud)
    with pytest.raises(DomainError) as raised:
        routed = await router.open_stream(ChatRequest(messages=[]))
        [item async for item in routed.deltas]
    assert raised.value.code == "ROUTING_CLOUD_UNAVAILABLE"


@pytest.mark.asyncio
async def test_local_preflight_failure_is_not_sent_to_cloud_in_local_only():
    local = Fake(error=DomainError("OLLAMA_MODEL_NOT_INSTALLED", "missing", 503, True))
    cloud = Fake(["cloud"])
    router = ModelRouter(runtime=runtime("local_only"), local=local, cloud=cloud)
    with pytest.raises(DomainError) as raised:
        await router.open_stream(ChatRequest(messages=[]))
    assert raised.value.code == "LOCAL_MODEL_UNAVAILABLE"
    assert cloud.calls == 0


@pytest.mark.asyncio
async def test_automatic_preserves_ollama_unavailable_reason_from_service_preflight():
    class Service:
        async def preflight_model(self, model):
            raise DomainError("OLLAMA_UNAVAILABLE", "offline", 503, True)

    local, cloud = Fake(["local"]), Fake(["cloud"])
    router = ModelRouter(
        runtime=runtime("automatic"),
        local=local,
        cloud=cloud,
        local_service=Service(),
    )
    routed = await router.open_stream(ChatRequest(messages=[]))
    assert routed.route.fallback_reason == "OLLAMA_UNAVAILABLE"
