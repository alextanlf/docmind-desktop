from __future__ import annotations

import httpx
import pytest
import respx

from app.api.errors import DomainError
from app.schemas.remote import CreateRemoteDocumentRequest, UpdateRemoteDocumentRequest
from app.yuque.api_gateway import YuqueApiGateway


def gateway(token: str | None = "token") -> YuqueApiGateway:
    return YuqueApiGateway(lambda: token)


@respx.mock
async def test_lists_repositories_and_documents_with_stable_ids() -> None:
    respx.get("https://www.yuque.com/api/v2/user").mock(
        return_value=httpx.Response(200, json={"data": {"login": "tan"}})
    )
    respx.get("https://www.yuque.com/api/v2/users/tan/repos").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "name": "产品文档",
                        "namespace": "tan",
                        "slug": "product",
                        "items_count": 2,
                    }
                ]
            },
        )
    )
    respx.get("https://www.yuque.com/api/v2/repos/tan/product/docs").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"id": 11, "title": "需求", "slug": "requirements"},
                    {"id": 12, "title": "设计", "slug": "design"},
                ]
            },
        )
    )

    repositories = await gateway().list_repositories()
    documents = await gateway().list_documents("tan/product")

    assert len(repositories) == 1
    assert repositories[0].remote_id == "tan/product"
    assert repositories[0].name == "产品文档"
    assert repositories[0].url == "https://www.yuque.com/tan/product"
    assert documents[0].remote_id == "tan/product/requirements"
    assert documents[0].url == "https://www.yuque.com/tan/product/requirements"


@respx.mock
async def test_reads_and_updates_document_content_via_api() -> None:
    detail_route = respx.get(
        "https://www.yuque.com/api/v2/repos/tan/product/docs/requirements"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "id": 11,
                    "title": "需求",
                    "slug": "requirements",
                    "body": "# 需求\n\n正文",
                }
            },
        )
    )
    update_route = respx.put("https://www.yuque.com/api/v2/repos/tan/product/docs/11").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "id": 11,
                    "title": "新版需求",
                    "slug": "requirements",
                    "body": "# 新版需求",
                }
            },
        )
    )
    document = gateway()

    content = await document.read_document("tan/product/requirements")
    updated = await document.update_document(
        UpdateRemoteDocumentRequest(
            document_id="tan/product/requirements",
            title="新版需求",
            content="# 新版需求",
        )
    )

    assert content.content == "# 需求\n\n正文"
    assert content.repository_id == "tan/product"
    assert updated.title == "新版需求"
    assert detail_route.called
    assert update_route.called
    request_payload = update_route.calls.last.request.content
    assert b'"title"' in request_payload
    assert b'"body"' in request_payload


@respx.mock
async def test_creates_document_in_requested_repository() -> None:
    route = respx.post("https://www.yuque.com/api/v2/repos/tan/product/docs").mock(
        return_value=httpx.Response(
            201,
            json={
                "data": {
                    "id": 13,
                    "title": "发布说明",
                    "slug": "release-notes",
                }
            },
        )
    )

    created = await gateway().create_document(
        CreateRemoteDocumentRequest(
            repository_id="tan/product",
            title="发布说明",
            content="# v1",
        )
    )

    assert created.remote_id == "tan/product/release-notes"
    assert created.repository_id == "tan/product"
    assert route.called


@respx.mock
async def test_maps_invalid_token_without_exposing_response_body() -> None:
    respx.get("https://www.yuque.com/api/v2/user").mock(
        return_value=httpx.Response(401, text="private upstream detail")
    )

    with pytest.raises(DomainError) as error:
        await gateway("bad-token").list_repositories()

    assert error.value.code == "YUQUE_API_AUTH_FAILED"
    assert "private upstream detail" not in error.value.message
