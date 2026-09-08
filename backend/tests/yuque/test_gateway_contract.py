from __future__ import annotations

import asyncio

import pytest

from app.schemas.yuque import (
    CreateRepositoryRequest,
    CreateYuqueDocumentRequest,
    UpdateYuqueDocumentRequest,
)
from app.yuque.gateway import FakeYuqueGateway


@pytest.fixture
def fake_yuque() -> FakeYuqueGateway:
    return FakeYuqueGateway()


async def test_fake_gateway_supports_repository_and_document_crud(fake_yuque: FakeYuqueGateway) -> None:
    repository = await fake_yuque.create_repository(CreateRepositoryRequest(name="SwiftUI"))
    created = await fake_yuque.create_document(
        CreateYuqueDocumentRequest(
            repository_id=repository.yuque_id,
            title="State",
            content="# State",
        )
    )

    assert repository.yuque_id == "repo-1"
    assert await fake_yuque.list_repositories() == [repository]
    assert created.yuque_id == "doc-1"
    assert (await fake_yuque.list_documents(repository.yuque_id))[0].title == "State"
    assert (await fake_yuque.read_document(created.yuque_id)).content == "# State"

    updated = await fake_yuque.update_document(
        UpdateYuqueDocumentRequest(
            document_id=created.yuque_id,
            title="State 2",
            content="# State 2",
        )
    )

    assert updated.title == "State 2"
    await fake_yuque.delete_document(created.yuque_id, repository.yuque_id)
    assert await fake_yuque.list_documents(repository.yuque_id) == []


async def test_gateway_serializes_context_access(fake_yuque: FakeYuqueGateway) -> None:
    await asyncio.gather(*(fake_yuque.login_status() for _ in range(10)))

    assert fake_yuque.max_concurrent_contexts == 1


async def test_fake_gateway_reports_successful_login_and_masked_account(
    fake_yuque: FakeYuqueGateway,
) -> None:
    result = await fake_yuque.begin_login()
    status = await fake_yuque.login_status()

    assert result.logged_in is True
    assert status.logged_in is True
    assert status.requires_login is False
    assert status.account_label == "f***e"


async def test_fake_gateway_rejects_unknown_document(fake_yuque: FakeYuqueGateway) -> None:
    with pytest.raises(Exception) as error:
        await fake_yuque.read_document("doc-missing")

    assert getattr(error.value, "code", None) == "YUQUE_PAGE_CHANGED"


async def test_fake_gateway_discovers_marker_and_verifies_document_absence(
    fake_yuque: FakeYuqueGateway,
) -> None:
    """Recovery lookup is content-specific and absence checks are explicit."""
    repository = await fake_yuque.create_repository(CreateRepositoryRequest(name="SwiftUI"))
    created = await fake_yuque.create_document(
        CreateYuqueDocumentRequest(
            repository_id=repository.yuque_id,
            title="State",
            content="# State\n\n<!-- docmind-mutation:abc -->",
        )
    )

    found = await fake_yuque.find_document_by_marker(
        repository.yuque_id, "docmind-mutation:abc"
    )

    assert found == created
    assert await fake_yuque.find_document_by_marker(repository.yuque_id, "missing") is None
    assert await fake_yuque.document_exists(repository.yuque_id, created.yuque_id) is True
    await fake_yuque.delete_document(created.yuque_id, repository.yuque_id)
    assert await fake_yuque.document_exists(repository.yuque_id, created.yuque_id) is False
