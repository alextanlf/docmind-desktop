import asyncio
import json

import httpx
import pytest

from app.api.errors import DomainError
from app.core.ollama import PullCoordinator
from app.schemas.ollama import OllamaConfig
from app.storage.repositories import OllamaPullStore


def ndjson_transport(lines):
    payload = b"".join((json.dumps(line).encode() + b"\n") for line in lines)

    def handler(request):
        return httpx.Response(200, content=payload)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_coordinator_reuses_active_address_and_clamps_progress(database):
    coordinator = PullCoordinator(
        config=OllamaConfig(),
        store=OllamaPullStore(database),
        transport=ndjson_transport(
            [
                {"status": "downloading", "total": 100, "completed": 150},
                {"status": "success", "done": True},
            ]
        ),
    )
    first = await coordinator.start("qwen2.5:7b")
    second = await coordinator.start("qwen2.5:7b")
    assert first.id == second.id
    await coordinator.wait_for_idle()
    assert coordinator.get(first.id).progress == 100
    assert coordinator.get(first.id).state == "completed"


@pytest.mark.asyncio
async def test_coordinator_cancel_publishes_error_terminal_without_raw_payload(database):
    release = asyncio.Event()

    class BlockingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"status":"starting"}\n'
            await release.wait()

        async def aclose(self):
            release.set()

    def handler(request):
        return httpx.Response(200, stream=BlockingStream())

    coordinator = PullCoordinator(
        config=OllamaConfig(),
        store=OllamaPullStore(database),
        transport=httpx.MockTransport(handler),
    )
    pull = await coordinator.start("qwen2.5:7b")
    subscription = coordinator.subscribe(pull.id, after_sequence=0)
    await asyncio.sleep(0)
    await coordinator.cancel(pull.id)
    events = [event async for event in subscription]
    assert events[-1].type == "error"
    assert events[-1].payload["code"] == "OLLAMA_PULL_CANCELLED"
    assert all("raw" not in event.payload for event in events)


@pytest.mark.asyncio
async def test_coordinator_rejects_different_model_while_address_active(database):
    coordinator = PullCoordinator(
        config=OllamaConfig(),
        store=OllamaPullStore(database),
        transport=ndjson_transport([{"status": "waiting"}]),
    )
    await coordinator.start("qwen2.5:7b")
    with pytest.raises(DomainError) as raised:
        await coordinator.start("llama3.2")
    assert raised.value.code == "OLLAMA_PULL_FAILED"
