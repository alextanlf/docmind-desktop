from pathlib import Path
from uuid import uuid4

import pytest

from app.schemas.batches import DiscoveryRequest
from app.schemas.yuque import CreateRepositoryRequest, CreateYuqueDocumentRequest
from app.yuque.discovery import YuqueDiscovery
from app.yuque.gateway import FakeYuqueGateway


class _Repos:
    def get(self, _id):
        return None


@pytest.mark.asyncio
async def test_yuque_discovery_uses_only_read_methods(tmp_path: Path) -> None:
    gateway = FakeYuqueGateway()
    repo = await gateway.create_repository(CreateRepositoryRequest(name="fixture"))
    await gateway.create_document(
        CreateYuqueDocumentRequest(repository_id=repo.yuque_id, title="A", content="# A")
    )
    gateway.write_calls.clear()  # fixture setup is outside the operation under test
    request = DiscoveryRequest(
        batch_id=uuid4(),
        source_kind="yuque_repository",
        repository_id=uuid4(),
        source_descriptor={"repositoryId": repo.yuque_id},
    )
    async def emit(_event):
        return None

    result = await YuqueDiscovery(gateway, _Repos(), tmp_path).discover(request, emit)
    assert len(result.sources) == 1
    assert gateway.write_calls == []
    assert gateway.read_calls == ["list_documents", "read_document"]
