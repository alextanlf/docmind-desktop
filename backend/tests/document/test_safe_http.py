from ipaddress import ip_address

import httpx
import pytest

from app.document.safe_http import SafeHttpClient, canonicalize_url, path_prefix_for_entry


class Resolver:
    async def resolve(self, host: str):
        return [ip_address("93.184.216.34")]


class MappingResolver:
    def __init__(self, mapping):
        self.mapping = mapping

    async def resolve(self, host: str):
        return self.mapping.get(host, [ip_address("93.184.216.34")])


def test_url_normalization_and_prefix() -> None:
    assert path_prefix_for_entry("https://docs.test/guide") == "/guide/"
    assert path_prefix_for_entry("https://docs.test/guide/") == "/guide/"
    assert canonicalize_url("https://docs.test/a?b=1&utm_medium=x#frag") == "https://docs.test/a?b=1"


@pytest.mark.asyncio
async def test_private_redirect_is_rejected_before_second_request() -> None:
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://private.test/secret"})

    client = SafeHttpClient(
        resolver=MappingResolver({"public.test": [ip_address("93.184.216.34")], "private.test": [ip_address("127.0.0.1")]}),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(Exception) as exc:
        await client.get("https://public.test/start", max_bytes=1024)
    assert getattr(exc.value, "code", None) == "CRAWL_SCOPE_REJECTED"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_get_rejects_oversized_response() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"12345"))
    client = SafeHttpClient(resolver=Resolver(), transport=transport)
    with pytest.raises(Exception) as exc:
        await client.get("https://docs.test/", max_bytes=4)
    assert getattr(exc.value, "code", None) == "DOCUMENT_TOO_LARGE"


@pytest.mark.asyncio
async def test_redirect_outside_scope_is_rejected_before_request() -> None:
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://other.test/private"})

    client = SafeHttpClient(resolver=Resolver(), transport=httpx.MockTransport(handler))
    with pytest.raises(Exception) as exc:
        await client.get("https://docs.test/guide/start", max_bytes=1024,
                         scope_origin="https://docs.test/guide/", scope_prefix="/guide/")
    assert getattr(exc.value, "code", None) == "CRAWL_SCOPE_REJECTED"
    assert calls == ["https://docs.test/guide/start"]
