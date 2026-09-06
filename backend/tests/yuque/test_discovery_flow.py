from pathlib import Path
from uuid import uuid4

import pytest

from app.schemas.batches import DiscoveryRequest
from app.schemas.yuque import CreateYuqueDocumentRequest
from app.yuque.discovery import YuqueDiscovery
from app.yuque.gateway import FakeYuqueGateway


class RepoStore:
    def get(self, _id):
        return None


@pytest.mark.asyncio
async def test_yuque_discovery_is_read_only_and_binds_remote_documents(tmp_path: Path):
    gateway = FakeYuqueGateway()
    await gateway.begin_login()
    repo = await gateway.create_repository(__import__('app.schemas.yuque', fromlist=['CreateRepositoryRequest']).CreateRepositoryRequest(name="Docs"))
    await gateway.create_document(CreateYuqueDocumentRequest(repository_id=repo.yuque_id, title="One", content="hello\n<!-- docmind marker -->"))
    req = DiscoveryRequest(batch_id=uuid4(), source_kind="yuque_repository", repository_id=uuid4(), source_descriptor={"repositoryId": repo.yuque_id})
    async def emit(_event):
        return None
    result = await YuqueDiscovery(gateway, RepoStore(), tmp_path).discover(req, emit)
    assert len(result.sources) == 1
    source = result.sources[0]
    assert source.remote_binding.repository_id == repo.yuque_id
    assert source.remote_binding.document_id.startswith("doc-")
    assert "marker" not in (tmp_path / "remote" / str(req.batch_id) / str(source.cached_source.cache_id)).read_text()
