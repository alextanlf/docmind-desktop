from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.errors import DomainError
from app.remote.discovery import RemoteDiscovery
from app.remote.fake import FakeRemoteProvider
from app.remote.registry import ProviderRegistry
from app.remote.snapshot import read_remote_snapshot
from app.schemas.batches import DiscoveryRequest, RemoteBinding
from app.schemas.remote import CreateRemoteDocumentRequest, CreateRemoteRepositoryRequest


class RepoStore:
    def get(self, _id):
        return None


def _registry(provider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(provider, always_configured=True)
    return registry


@pytest.mark.asyncio
async def test_remote_discovery_is_read_only_and_binds_remote_documents(tmp_path: Path):
    provider = FakeRemoteProvider()
    await provider.begin_login()
    repo = await provider.create_repository(CreateRemoteRepositoryRequest(name="Docs"))
    await provider.create_document(
        CreateRemoteDocumentRequest(
            repository_id=repo.remote_id,
            title="One",
            content="hello\n<!-- docmind marker -->",
        )
    )
    request = DiscoveryRequest(
        batch_id=uuid4(),
        source_kind="remote_repository",
        repository_id=uuid4(),
        source_descriptor={"provider": "yuque", "repositoryId": repo.remote_id},
    )

    async def emit(_event):
        return None

    result = await RemoteDiscovery(_registry(provider), RepoStore(), tmp_path).discover(
        request, emit
    )
    assert len(result.sources) == 1
    source = result.sources[0]
    assert source.remote_binding.provider == "yuque"
    assert source.remote_binding.repository_id == repo.remote_id
    assert source.remote_binding.document_id.startswith("doc-")
    assert source.source_identity == f"yuque:{repo.remote_id}:{source.remote_binding.document_id}"
    cached = tmp_path / "remote" / str(request.batch_id) / str(source.cached_source.cache_id)
    assert "marker" not in cached.read_text()


@pytest.mark.asyncio
async def test_remote_discovery_rejects_a_non_remote_source_kind(tmp_path: Path) -> None:
    request = DiscoveryRequest(
        batch_id=uuid4(),
        source_kind="web",
        repository_id=uuid4(),
        source_descriptor={"provider": "yuque", "repositoryId": "docs/one"},
    )

    async def emit(_event):
        return None

    with pytest.raises(DomainError) as error:
        await RemoteDiscovery(_registry(FakeRemoteProvider()), RepoStore(), tmp_path).discover(
            request, emit
        )
    assert error.value.code == "INVALID_REQUEST"


@pytest.mark.asyncio
async def test_read_remote_snapshot_hashes_content() -> None:
    provider = FakeRemoteProvider()
    await provider.begin_login()
    repo = await provider.create_repository(CreateRemoteRepositoryRequest(name="Docs"))
    await provider.create_document(
        CreateRemoteDocumentRequest(repository_id=repo.remote_id, title="One", content="# One")
    )

    states = await read_remote_snapshot(provider, repo.remote_id)

    assert len(states) == 1
    state, content = states[0]
    assert state.title == "One"
    assert state.document_id.startswith("doc-")
    assert len(state.content_sha256) == 64
    assert content == "# One"


class TestRemoteBindingProviderIsRequired:
    """`provider` used to default to "yuque", so any binding that omitted it
    silently landed on Yuque. The field is now mandatory."""

    def test_provider_is_required(self) -> None:
        with pytest.raises(ValidationError):
            RemoteBinding(repository_id="r1", document_id="d1")

    def test_explicit_provider_round_trips(self) -> None:
        binding = RemoteBinding(provider="feishu", repository_id="r1", document_id="d1")
        assert binding.model_dump()["provider"] == "feishu"

    def test_empty_provider_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RemoteBinding(provider="", repository_id="r1", document_id="d1")
