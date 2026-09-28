from __future__ import annotations

import httpx
import pytest
import respx

from app.api.errors import DomainError
from app.core.secrets import MemorySecretStore
from app.feishu.provider import FeishuProvider
from app.feishu.tokens import FEISHU_USER_SECRET_REF, FeishuTokenManager, UserTokenBundle
from app.remote.credentials import CredentialStore
from app.schemas.remote import CreateRemoteDocumentRequest, UpdateRemoteDocumentRequest
from app.storage.database import Database

BASE = "https://open.feishu.cn/open-apis"


@pytest.fixture
def store(database: Database) -> CredentialStore:
    store = CredentialStore(database, MemorySecretStore())
    store.save_secret(
        "feishu",
        "user",
        UserTokenBundle("u-1", None, None, "谭凌峰").to_json(),
        FEISHU_USER_SECRET_REF,
    )
    store.mark_state("feishu", "user", "verified", account_label="谭凌峰")
    return store


@pytest.fixture
def provider(store: CredentialStore) -> FeishuProvider:
    return FeishuProvider(FeishuTokenManager(store))


def _space_payload(space_id: str = "sp1", name: str = "产品文档") -> dict:
    return {
        "code": 0,
        "data": {"items": [{"space_id": space_id, "name": name}], "has_more": False},
    }


@respx.mock
async def test_login_status_uses_stored_user_bundle(provider: FeishuProvider) -> None:
    status = await provider.login_status()

    assert status.logged_in is True
    assert status.account_label == "谭***峰"


async def test_login_status_requires_login_without_user_channel(
    database: Database,
) -> None:
    provider = FeishuProvider(FeishuTokenManager(CredentialStore(database, MemorySecretStore())))

    status = await provider.login_status()

    assert status.logged_in is False
    assert status.requires_login is True


@respx.mock
async def test_lists_wiki_spaces_as_repositories(provider: FeishuProvider) -> None:
    respx.get(f"{BASE}/wiki/v2/spaces").mock(return_value=httpx.Response(200, json=_space_payload()))

    repositories = await provider.list_repositories()

    assert len(repositories) == 1
    assert repositories[0].remote_id == "sp1"
    assert repositories[0].name == "产品文档"


@respx.mock
async def test_walks_wiki_tree_and_flattens_docx_nodes(provider: FeishuProvider) -> None:
    route = respx.get(f"{BASE}/wiki/v2/spaces/sp1/nodes").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "node_token": "wikDir",
                                "obj_type": "docx",
                                "title": "目录页",
                                "has_child": True,
                            },
                            {
                                "node_token": "wikSheet",
                                "obj_type": "sheet",
                                "title": "表格",
                                "has_child": False,
                            },
                        ],
                        "has_more": False,
                    },
                },
            ),
            httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "node_token": "wikChild",
                                "obj_type": "docx",
                                "title": "子页",
                                "has_child": False,
                            }
                        ],
                        "has_more": False,
                    },
                },
            ),
        ]
    )

    documents = await provider.list_documents("sp1")

    assert [doc.remote_id for doc in documents] == ["wikDir", "wikChild"]
    assert documents[0].url == "https://feishu.cn/wiki/wikDir"
    # The sheet node is skipped, and the child listing used the parent token.
    assert route.calls[1].request.url.params["parent_node_token"] == "wikDir"


@respx.mock
async def test_create_document_writes_markdown_via_convert_and_descendant(
    provider: FeishuProvider,
) -> None:
    create_route = respx.post(f"{BASE}/wiki/v2/spaces/sp1/nodes").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "node": {"node_token": "wikNew", "obj_token": "docxNew", "obj_type": "docx"}
                },
            },
        )
    )
    respx.post(f"{BASE}/docx/v1/documents/blocks/convert").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "first_level_block_ids": ["b1"],
                    "blocks": [{"block_id": "b1", "block_type": 2}],
                },
            },
        )
    )
    respx.get(f"{BASE}/docx/v1/documents/docxNew/blocks/docxNew/children").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": {"items": [], "has_more": False}})
    )
    insert_route = respx.post(
        f"{BASE}/docx/v1/documents/docxNew/blocks/docxNew/descendant"
    ).mock(return_value=httpx.Response(200, json={"code": 0, "data": {}}))

    document = await provider.create_document(
        CreateRemoteDocumentRequest(
            repository_id="sp1",
            title="新文档",
            content="正文",
            parent_id="wikParent",
        )
    )

    assert document.remote_id == "wikNew"
    assert document.url == "https://feishu.cn/wiki/wikNew"
    assert insert_route.called
    import json as _json

    assert _json.loads(create_route.calls[0].request.content)["parent_node_token"] == "wikParent"


@respx.mock
async def test_read_document_reconstructs_markdown_and_strips_mutation_marker(
    provider: FeishuProvider,
) -> None:
    respx.get(f"{BASE}/wiki/v2/spaces/get_node").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "node": {
                        "node_token": "wik1",
                        "obj_token": "docx1",
                        "obj_type": "docx",
                        "space_id": "sp1",
                        "title": "指南",
                    }
                },
            },
        )
    )
    respx.get(f"{BASE}/docx/v1/documents/docx1/blocks").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "items": [
                        {"block_id": "page", "block_type": 1, "children": ["h1", "p"]},
                        {
                            "block_id": "h1",
                            "parent_id": "page",
                            "block_type": 3,
                            "heading1": {"elements": [{"text_run": {"content": "标题"}}]},
                        },
                        {
                            "block_id": "p",
                            "parent_id": "page",
                            "block_type": 2,
                            "text": {
                                "elements": [
                                    {"text_run": {"content": "正文文本\n\n<!-- docmind-mutation:m1 -->"}}
                                ]
                            },
                        },
                    ],
                    "has_more": False,
                },
            },
        )
    )

    content = await provider.read_document("wik1")

    assert content.title == "指南"
    assert content.repository_id == "sp1"
    assert content.content == "# 标题\n\n正文文本"


@respx.mock
async def test_find_document_by_marker_uses_user_search_before_walking(
    provider: FeishuProvider,
) -> None:
    search_route = respx.post(f"{BASE}/suite/docs-api/search/object").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "docs_entities": [
                        {"docs_token": "docx1", "docs_type": "docx", "title": "Guide"}
                    ],
                    "has_more": False,
                },
            },
        )
    )
    respx.get(f"{BASE}/wiki/v2/spaces/get_node").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "node": {
                        "node_token": "wik1",
                        "obj_token": "docx1",
                        "obj_type": "docx",
                        "space_id": "sp1",
                        "title": "指南",
                    }
                },
            },
        )
    )
    respx.get(f"{BASE}/docx/v1/documents/docx1/blocks").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "items": [
                        {
                            "block_id": "p",
                            "block_type": 2,
                            "text": {
                                "elements": [
                                    {"text_run": {"content": "<!-- docmind-mutation:abc -->"}}
                                ]
                            },
                        }
                    ],
                    "has_more": False,
                },
            },
        )
    )
    walk_route = respx.get(f"{BASE}/wiki/v2/spaces/sp1/nodes").mock(
        return_value=httpx.Response(500)
    )

    found = await provider.find_document_by_marker("sp1", "docmind-mutation:abc")

    assert found is not None
    assert found.remote_id == "wik1"
    assert search_route.called
    assert not walk_route.called


@respx.mock
async def test_find_document_by_marker_falls_back_to_walk_when_search_misses(
    provider: FeishuProvider,
) -> None:
    respx.post(f"{BASE}/suite/docs-api/search/object").mock(
        return_value=httpx.Response(
            200,
            json={"code": 0, "data": {"docs_entities": [], "has_more": False}},
        )
    )
    respx.get(f"{BASE}/wiki/v2/spaces/sp1/nodes").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "items": [
                        {
                            "node_token": "wik1",
                            "obj_token": "docx1",
                            "obj_type": "docx",
                            "title": "指南",
                            "has_child": False,
                        }
                    ],
                    "has_more": False,
                },
            },
        )
    )
    respx.get(f"{BASE}/wiki/v2/spaces/get_node").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "node": {
                        "node_token": "wik1",
                        "obj_token": "docx1",
                        "obj_type": "docx",
                        "space_id": "sp1",
                        "title": "指南",
                    }
                },
            },
        )
    )
    respx.get(f"{BASE}/docx/v1/documents/docx1/blocks").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "items": [
                        {
                            "block_id": "p",
                            "block_type": 2,
                            "text": {
                                "elements": [
                                    {"text_run": {"content": "<!-- docmind-mutation:abc -->"}}
                                ]
                            },
                        }
                    ],
                    "has_more": False,
                },
            },
        )
    )

    found = await provider.find_document_by_marker("sp1", "docmind-mutation:abc")

    assert found is not None
    assert found.remote_id == "wik1"


@respx.mock
async def test_update_document_replaces_body_and_renames_node(provider: FeishuProvider) -> None:
    respx.get(f"{BASE}/wiki/v2/spaces/get_node").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "node": {
                        "node_token": "wik1",
                        "obj_token": "docx1",
                        "obj_type": "docx",
                        "space_id": "sp1",
                        "title": "旧标题",
                    }
                },
            },
        )
    )
    respx.post(f"{BASE}/docx/v1/documents/blocks/convert").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {"first_level_block_ids": ["b1"], "blocks": [{"block_id": "b1"}]},
            },
        )
    )
    respx.get(f"{BASE}/docx/v1/documents/docx1/blocks/docx1/children").mock(
        return_value=httpx.Response(
            200,
            json={"code": 0, "data": {"items": [{"block_id": "old1"}, {"block_id": "old2"}], "has_more": False}},
        )
    )
    delete_route = respx.delete(
        f"{BASE}/docx/v1/documents/docx1/blocks/docx1/children/batch_delete"
    ).mock(return_value=httpx.Response(200, json={"code": 0, "data": {}}))
    respx.post(f"{BASE}/docx/v1/documents/docx1/blocks/docx1/descendant").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": {}})
    )
    rename_route = respx.post(f"{BASE}/wiki/v2/spaces/sp1/nodes/wik1/update_title").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": {}})
    )

    document = await provider.update_document(
        UpdateRemoteDocumentRequest(document_id="wik1", title="新标题", content="新正文")
    )

    assert document.title == "新标题"
    assert document.repository_id == "sp1"
    import json as _json

    assert _json.loads(delete_route.calls[0].request.content) == {"start_index": 0, "end_index": 2}
    assert _json.loads(rename_route.calls[0].request.content) == {"title": "新标题"}


@respx.mock
async def test_delete_document_trashes_underlying_docx(provider: FeishuProvider) -> None:
    respx.get(f"{BASE}/wiki/v2/spaces/get_node").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "node": {"node_token": "wik1", "obj_token": "docx1", "space_id": "sp1"}
                },
            },
        )
    )
    delete_route = respx.delete(f"{BASE}/drive/v1/files/docx1").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": {}})
    )

    await provider.delete_document("wik1", "sp1")

    assert delete_route.calls[0].request.url.params["type"] == "docx"


@respx.mock
async def test_document_exists_checks_space_membership(provider: FeishuProvider) -> None:
    route = respx.get(f"{BASE}/wiki/v2/spaces/get_node").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"node": {"node_token": "wik1", "obj_token": "d1", "space_id": "sp1"}}
                },
            ),
            httpx.Response(200, json={"code": 131006, "msg": "node not found"}),
        ]
    )

    assert await provider.document_exists("sp1", "wik1") is True
    assert await provider.document_exists("sp1", "wikGone") is False
    assert route.call_count == 2


async def test_operations_require_credentials(database: Database) -> None:
    provider = FeishuProvider(FeishuTokenManager(CredentialStore(database, MemorySecretStore())))

    with pytest.raises(DomainError) as caught:
        await provider.list_repositories()
    assert caught.value.code == "FEISHU_LOGIN_REQUIRED"
