from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.api.errors import DomainError
from app.remote.fake import FakeRemoteProvider
from app.schemas.remote import (
    CreateRemoteDocumentRequest,
    CreateRemoteRepositoryRequest,
    UpdateRemoteDocumentRequest,
)


@pytest.fixture
def fake_remote() -> FakeRemoteProvider:
    return FakeRemoteProvider()


def test_fake_provider_exposes_a_provider_neutral_identity(fake_remote: FakeRemoteProvider) -> None:
    assert fake_remote.identity.name == "yuque"
    assert fake_remote.identity.label == "语雀"
    assert fake_remote.identity.capabilities.browser_install is True
    assert fake_remote.identity.capabilities.marker_lookup is True


async def test_fake_provider_supports_repository_and_document_crud(
    fake_remote: FakeRemoteProvider,
) -> None:
    repository = await fake_remote.create_repository(CreateRemoteRepositoryRequest(name="SwiftUI"))
    created = await fake_remote.create_document(
        CreateRemoteDocumentRequest(
            repository_id=repository.remote_id,
            title="State",
            content="# State",
        )
    )

    assert repository.remote_id == "repo-1"
    assert repository.url == "https://remote.local/repo-1"
    assert await fake_remote.list_repositories() == [repository]
    assert created.remote_id == "doc-1"
    assert created.url == "https://remote.local/doc-1"
    assert (await fake_remote.list_documents(repository.remote_id))[0].title == "State"
    assert (await fake_remote.read_document(created.remote_id)).content == "# State"

    updated = await fake_remote.update_document(
        UpdateRemoteDocumentRequest(
            document_id=created.remote_id,
            title="State 2",
            content="# State 2",
        )
    )

    assert updated.title == "State 2"
    await fake_remote.delete_document(created.remote_id, repository.remote_id)
    assert await fake_remote.list_documents(repository.remote_id) == []


async def test_fake_provider_serializes_context_access(fake_remote: FakeRemoteProvider) -> None:
    await asyncio.gather(*(fake_remote.login_status() for _ in range(10)))

    assert fake_remote.max_concurrent_contexts == 1


async def test_fake_provider_reports_successful_login_and_masked_account(
    fake_remote: FakeRemoteProvider,
) -> None:
    result = await fake_remote.begin_login()
    status = await fake_remote.login_status()

    assert result.logged_in is True
    assert status.logged_in is True
    assert status.requires_login is False
    assert status.account_label == "f***e"


async def test_fake_provider_rejects_unknown_document_and_repository(
    fake_remote: FakeRemoteProvider,
) -> None:
    with pytest.raises(DomainError) as document_error:
        await fake_remote.read_document("doc-missing")
    assert document_error.value.code == "REMOTE_NOT_FOUND"
    assert document_error.value.status_code == 404

    with pytest.raises(DomainError) as repository_error:
        await fake_remote.list_documents("repo-missing")
    assert repository_error.value.code == "REMOTE_NOT_FOUND"


async def test_fake_provider_discovers_marker_and_verifies_document_absence(
    fake_remote: FakeRemoteProvider,
) -> None:
    """Recovery lookup is content-specific and absence checks are explicit."""
    repository = await fake_remote.create_repository(CreateRemoteRepositoryRequest(name="SwiftUI"))
    created = await fake_remote.create_document(
        CreateRemoteDocumentRequest(
            repository_id=repository.remote_id,
            title="State",
            content="# State\n\n<!-- docmind-mutation:abc -->",
        )
    )

    found = await fake_remote.find_document_by_marker(
        repository.remote_id, "docmind-mutation:abc"
    )

    assert found == created
    assert await fake_remote.find_document_by_marker(repository.remote_id, "missing") is None
    assert await fake_remote.document_exists(repository.remote_id, created.remote_id) is True
    await fake_remote.delete_document(created.remote_id, repository.remote_id)
    assert await fake_remote.document_exists(repository.remote_id, created.remote_id) is False


async def test_fake_provider_strips_the_mutation_marker_on_read(
    fake_remote: FakeRemoteProvider,
) -> None:
    repository = await fake_remote.create_repository(CreateRemoteRepositoryRequest(name="Docs"))
    created = await fake_remote.create_document(
        CreateRemoteDocumentRequest(
            repository_id=repository.remote_id,
            title="State",
            content="# State\n\n<!-- docmind-mutation:abc -->",
        )
    )

    assert (await fake_remote.read_document(created.remote_id)).content == "# State"


def test_seed_repository_is_a_read_only_fixture_helper(fake_remote: FakeRemoteProvider) -> None:
    repository = fake_remote.seed_repository("docs/one", "One")

    assert repository.remote_id == "docs/one"
    assert repository.url == "https://remote.local/docs/one"
    assert fake_remote.write_calls == []


@pytest.mark.asyncio
async def test_fake_provider_persists_e2e_state_under_provider_neutral_paths(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    provider = FakeRemoteProvider(data_dir)
    await provider.begin_login()
    repository = await provider.create_repository(CreateRemoteRepositoryRequest(name="Docs"))
    await provider.create_document(
        CreateRemoteDocumentRequest(repository_id=repository.remote_id, title="One", content="# One")
    )

    assert (data_dir / "e2e" / "remote-logged-in").exists()
    state = json.loads((data_dir / "e2e" / "remote-state.json").read_text(encoding="utf-8"))
    assert state["repositories"][0]["remoteId"] == repository.remote_id

    reloaded = FakeRemoteProvider(data_dir)
    assert (await reloaded.login_status()).logged_in is True
    assert (await reloaded.list_repositories())[0].remote_id == repository.remote_id
    assert (await reloaded.read_document("doc-1")).content == "# One"
