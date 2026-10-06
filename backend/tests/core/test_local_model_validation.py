from __future__ import annotations

import pytest

from app.api.errors import DomainError
from app.core.local_model_validation import normalize_loopback_base_url, validate_model_tag


class FakeResolver:
    def __init__(self, answers):
        self.answers = answers

    async def resolve(self, host):
        return self.answers


LOOPBACK = FakeResolver(["127.0.0.1"])


async def test_any_loopback_port_is_accepted():
    """Local servers are not all on 11434.

    LM Studio defaults to 1234 and llama.cpp's server to 8080, so the port is a
    server setting and must not be pinned to one value.
    """
    for url in (
        "http://127.0.0.1:11434",
        "http://127.0.0.1:1234",
        "http://127.0.0.1:8080",
        "http://127.0.0.1:65535",
    ):
        assert await normalize_loopback_base_url(url, FakeResolver([])) == url


async def test_port_is_preserved_rather_than_rewritten():
    normalized = await normalize_loopback_base_url("http://127.0.0.1:1234", FakeResolver([]))
    assert normalized.endswith(":1234")


async def test_trailing_slash_and_ipv6_host_are_normalized():
    assert (
        await normalize_loopback_base_url("http://[::1]:1234/", FakeResolver([]))
        == "http://[::1]:1234"
    )


async def test_localhost_requires_loopback_dns_answers():
    normalized = await normalize_loopback_base_url("http://localhost:1234", LOOPBACK)
    assert normalized == "http://localhost:1234"


async def test_dns_rebinding_answer_is_rejected():
    resolver = FakeResolver(["93.184.216.34"])
    with pytest.raises(DomainError) as error:
        await normalize_loopback_base_url("http://localhost:1234", resolver)
    assert error.value.code == "LOCAL_MODEL_UNAVAILABLE"


async def test_trusted_literal_rejects_non_loopback_answer():
    resolver = FakeResolver(["93.184.216.34"])
    with pytest.raises(DomainError):
        await normalize_loopback_base_url("http://127.0.0.1:1234", resolver)


@pytest.mark.parametrize(
    "value",
    [
        "http://user:pass@127.0.0.1:1234",
        "http://127.0.0.1:1234?a=b",
        "http://127.0.0.1:1234/path",
        "http://10.0.0.1:1234",
        "https://127.0.0.1:1234",
        "http://example.com:1234",
    ],
)
async def test_rejects_unsafe_urls(value):
    with pytest.raises(ValueError):
        await normalize_loopback_base_url(value, LOOPBACK)


@pytest.mark.parametrize(
    "value",
    ["http://127.0.0.2:1234", "http://127.1.0.1:1234", "http://[::ffff:127.0.0.1]:1234"],
)
async def test_rejects_loopback_addresses_other_than_the_two_allowed_literals(value):
    with pytest.raises(ValueError):
        await normalize_loopback_base_url(value, FakeResolver([]))


async def test_direct_allowed_literal_does_not_require_a_dns_answer():
    assert await normalize_loopback_base_url("http://127.0.0.1:1234", FakeResolver([]))


def test_model_tag_rejects_whitespace_and_delimiters():
    assert validate_model_tag("qwen2.5:0.5b") == "qwen2.5:0.5b"
    with pytest.raises(ValueError):
        validate_model_tag("has space")
    with pytest.raises(ValueError):
        validate_model_tag("")
    with pytest.raises(ValueError):
        validate_model_tag("a\nb")
