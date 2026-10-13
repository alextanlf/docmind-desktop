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
    respx.get("https://www.yuque.com/api/v2/repos/tan/product/toc").mock(
        return_value=httpx.Response(200, json={"data": _TOC})
    )

    repositories = await gateway().list_repositories()
    documents = await gateway().list_documents("tan/product")

    assert len(repositories) == 1
    assert repositories[0].remote_id == "tan/product"
    assert repositories[0].name == "产品文档"
    assert repositories[0].url == "https://www.yuque.com/tan/product"
    assert documents[0].remote_id == "tan/product/requirements"
    assert documents[0].url == "https://www.yuque.com/tan/product/requirements"
    # 目录是独立于文档列表的一份结构：路径只能从 TOC 推出来。
    assert documents[0].path == ["第一章", "第一节"]
    assert documents[1].path == []


#: 语雀 TOC 的形状：TITLE 是纯目录项，DOC 才是文档，靠 parent_uuid 串成树。
_TOC = [
    {"uuid": "t1", "type": "TITLE", "title": "第一章", "parent_uuid": "", "depth": 0},
    {"uuid": "t2", "type": "TITLE", "title": "第一节", "parent_uuid": "t1", "depth": 1},
    {
        "uuid": "d1",
        "type": "DOC",
        "title": "需求",
        "slug": "requirements",
        "parent_uuid": "t2",
        "depth": 2,
    },
    {"uuid": "d2", "type": "DOC", "title": "设计", "slug": "design", "parent_uuid": "", "depth": 0},
]


@respx.mock
async def test_missing_toc_degrades_to_flat_paths_instead_of_failing() -> None:
    """目录读不到时**不许连累文档列表**。

    目录接口对凭据/套餐的可见性与文档列表不一致（只读成员、团队版限制都可能让它
    403）。路径只是锦上添花；把它做成硬依赖，代价是"连文档都列不出来了"。
    """
    respx.get("https://www.yuque.com/api/v2/repos/tan/product/docs").mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": 11, "title": "需求", "slug": "requirements"}]}
        )
    )
    respx.get("https://www.yuque.com/api/v2/repos/tan/product/toc").mock(
        return_value=httpx.Response(403, json={"message": "forbidden"})
    )

    documents = await gateway().list_documents("tan/product")

    assert [document.remote_id for document in documents] == ["tan/product/requirements"]
    assert documents[0].path == []


@respx.mock
async def test_unexpected_toc_shape_yields_no_paths_rather_than_a_guess() -> None:
    """形状不认识时宁可平铺。编一层看起来像树的层级，按目录移除时会漏文档。"""
    respx.get("https://www.yuque.com/api/v2/repos/tan/product/docs").mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": 11, "title": "需求", "slug": "requirements"}]}
        )
    )
    respx.get("https://www.yuque.com/api/v2/repos/tan/product/toc").mock(
        return_value=httpx.Response(200, json={"data": {"unexpected": "shape"}})
    )

    documents = await gateway().list_documents("tan/product")

    assert documents[0].path == []


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
