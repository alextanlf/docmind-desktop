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


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [
    "http://127.0.0.2:11434",
    "http://127.1.0.1:11434",
    "http://[::ffff:127.0.0.1]:11434",
])
async def test_rejects_loopback_addresses_other_than_the_two_allowed_literals(value):
    with pytest.raises((ValueError, DomainError)):
        await normalize_loopback_base_url(value, FakeResolver({}))


@pytest.mark.asyncio
async def test_direct_allowed_literal_does_not_require_a_fake_dns_answer():
    assert await normalize_loopback_base_url("http://127.0.0.1:11434", FakeResolver({})) == "http://127.0.0.1:11434"


def test_model_tag_rejects_whitespace_and_delimiters():
    for value in ("qwen 2.5", "qwen\t2.5", "qwen\r2.5"):
        with pytest.raises(ValueError):
            validate_model_tag(value)


def test_rejects_control_chars_in_model_tag():
    with pytest.raises(ValueError):
        validate_model_tag("qwen2.5:7b\nignored")
