import pytest

from app.api.errors import DomainError
from app.core.ollama_validation import normalize_loopback_base_url, validate_model_tag


class FakeResolver:
    def __init__(self, mapping):
        self.mapping = mapping

    async def resolve(self, host):
        return self.mapping.get(host, [])


@pytest.mark.asyncio
async def test_localhost_is_allowed_only_when_every_dns_answer_is_loopback():
    resolver = FakeResolver({"localhost": ["127.0.0.1"]})
    assert await normalize_loopback_base_url("http://localhost:11434/", resolver) == "http://localhost:11434"


@pytest.mark.asyncio
async def test_dns_rebinding_answer_is_rejected():
    resolver = FakeResolver({"localhost": ["127.0.0.1", "192.168.1.4"]})
    with pytest.raises(DomainError) as raised:
        await normalize_loopback_base_url("http://localhost:11434", resolver)
    assert raised.value.code == "OLLAMA_UNAVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [
    "http://user:pass@127.0.0.1:11434",
    "http://127.0.0.1:11434?x=1",
    "http://127.0.0.1:11434/path",
    "http://10.0.0.1:11434",
])
async def test_rejects_unsafe_urls(value):
    with pytest.raises((ValueError, DomainError)):
        await normalize_loopback_base_url(value, FakeResolver({"127.0.0.1": ["127.0.0.1"], "10.0.0.1": ["10.0.0.1"]}))


def test_rejects_control_chars_in_model_tag():
    with pytest.raises(ValueError):
        validate_model_tag("qwen2.5:7b\nignored")
