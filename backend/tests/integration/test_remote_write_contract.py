from pathlib import Path
from uuid import uuid4

import pytest

from app.schemas.batches import DiscoveryRequest
from app.schemas.remote import CreateRemoteRepositoryRequest, CreateRemoteDocumentRequest
from app.remote.discovery import RemoteDiscovery
from app.remote.fake import FakeRemoteProvider
from app.remote.registry import ProviderRegistry


class _Repos:
    def get(self, _id):
        return None


@pytest.mark.asyncio
async def test_remote_discovery_uses_only_read_methods(tmp_path: Path) -> None:
    gateway = FakeRemoteProvider()
    repo = await gateway.create_repository(CreateRemoteRepositoryRequest(name="fixture"))
    await gateway.create_document(
        CreateRemoteDocumentRequest(repository_id=repo.remote_id, title="A", content="# A")
    )
    gateway.write_calls.clear()  # fixture setup is outside the operation under test
    registry = ProviderRegistry()
    registry.register(gateway, always_configured=True)
    request = DiscoveryRequest(
        batch_id=uuid4(),
        source_kind="remote_repository",
        repository_id=uuid4(),
        source_descriptor={"provider": "yuque", "repositoryId": repo.remote_id},
    )
    async def emit(_event):
        return None

    result = await RemoteDiscovery(registry, _Repos(), tmp_path).discover(request, emit)
    assert len(result.sources) == 1
    assert gateway.write_calls == []
    assert gateway.read_calls == ["list_documents", "read_document"]
