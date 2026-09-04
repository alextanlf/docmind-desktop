from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from app.document.safe_http import SafeHttpClient
from app.document.web_discovery import WebDiscovery
from app.schemas.batches import DiscoveryRequest


class Resolver:
    async def resolve(self, host):
        return ["93.184.216.34"]


@pytest.mark.asyncio
async def test_web_discovery_crawls_sitemap_links_and_snapshots(tmp_path: Path):
    routes = {
        "/robots.txt": (200, "text/plain", b"User-agent: *\nAllow: /") ,
        "/sitemap.xml": (200, "application/xml", b"<urlset><url><loc>https://docs.test/guide/a</loc></url></urlset>"),
        "/guide/": (200, "text/html", b"<html><title>Root</title><body><script>x</script><a href='/guide/a'>A</a></body></html>"),
        "/guide/a": (200, "text/markdown", b"# A\ncontent"),
    }
    def handler(req: httpx.Request):
        status, media, body = routes.get(req.url.path, (404, "text/plain", b""))
        return httpx.Response(status, headers={"content-type": media}, content=body, request=req)
    client = SafeHttpClient(resolver=Resolver(), transport=httpx.MockTransport(handler))
    req = DiscoveryRequest(batch_id=uuid4(), source_kind="web", repository_id=uuid4(), source_descriptor={"entryUrl":"https://docs.test/guide", "maxDepth":2, "maxPages":10, "useSitemap":True})
    events = []
    async def emit(event): events.append(event)
    result = await WebDiscovery(client, tmp_path).discover(req, emit)
    # Sitemap is outside the path prefix and therefore ignored; linked page is discovered.
    assert {s.display_path for s in result.sources} == {"https://docs.test/guide/a"}
    assert all((tmp_path / "remote" / str(req.batch_id) / str(s.cached_source.cache_id)).exists() for s in result.sources)
    assert result.rejected_count >= 0
    assert events[-1].stage == "awaiting_confirmation"
