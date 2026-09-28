from __future__ import annotations

import httpx
import pytest
import respx

from app.api.errors import DomainError
from app.feishu import client


@respx.mock
async def test_fetch_tenant_access_token_returns_token() -> None:
    respx.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal").mock(
        return_value=httpx.Response(
            200, json={"code": 0, "tenant_access_token": "t-abc", "expire": 7200}
        )
    )

    assert await client.fetch_tenant_access_token("cli_a", "secret") == "t-abc"


@respx.mock
async def test_fetch_tenant_access_token_rejects_bad_credentials() -> None:
    respx.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal").mock(
        return_value=httpx.Response(200, json={"code": 10003, "msg": "invalid app_secret"})
    )

    with pytest.raises(DomainError) as caught:
        await client.fetch_tenant_access_token("cli_a", "wrong")
    assert caught.value.code == "FEISHU_API_ERROR"
    assert "invalid app_secret" in caught.value.message


@respx.mock
async def test_envelope_auth_codes_map_to_auth_failed() -> None:
    respx.get("https://open.feishu.cn/open-apis/wiki/v2/spaces").mock(
        return_value=httpx.Response(200, json={"code": 99991663, "msg": "token expired"})
    )

    with pytest.raises(DomainError) as caught:
        await client.list_wiki_spaces("t-expired")
    assert caught.value.code == "FEISHU_API_AUTH_FAILED"


@respx.mock
async def test_list_wiki_spaces_paginates() -> None:
    route = respx.get("https://open.feishu.cn/open-apis/wiki/v2/spaces").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [{"space_id": "s1", "name": "产品"}],
                        "has_more": True,
                        "page_token": "p2",
                    },
                },
            ),
            httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"items": [{"space_id": "s2", "name": "研发"}], "has_more": False},
                },
            ),
        ]
    )

    spaces = await client.list_wiki_spaces("t-ok")

    assert [space["space_id"] for space in spaces] == ["s1", "s2"]
    assert route.calls[1].request.url.params["page_token"] == "p2"


@respx.mock
async def test_list_space_nodes_passes_parent_token() -> None:
    route = respx.get("https://open.feishu.cn/open-apis/wiki/v2/spaces/s1/nodes").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "items": [
                        {
                            "node_token": "wikcn1",
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

    nodes = await client.list_space_nodes("t-ok", "s1", "wikcnParent")

    assert nodes[0]["node_token"] == "wikcn1"
    assert route.calls[0].request.url.params["parent_node_token"] == "wikcnParent"


@respx.mock
async def test_get_wiki_node_maps_missing_node_to_not_found() -> None:
    respx.get("https://open.feishu.cn/open-apis/wiki/v2/spaces/get_node").mock(
        return_value=httpx.Response(200, json={"code": 131006, "msg": "node not found"})
    )

    with pytest.raises(DomainError) as caught:
        await client.get_wiki_node("t-ok", "wikcnMissing")
    assert caught.value.code == "FEISHU_API_NOT_FOUND"


@respx.mock
async def test_convert_markdown_strips_table_merge_info() -> None:
    respx.post("https://open.feishu.cn/open-apis/docx/v1/documents/blocks/convert").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "first_level_block_ids": ["b1", "tbl"],
                    "blocks": [
                        {
                            "block_id": "b1",
                            "block_type": 3,
                            "heading1": {"elements": [{"text_run": {"content": "标题"}}]},
                        },
                        {
                            "block_id": "tbl",
                            "block_type": 31,
                            "table": {"property": {"row_size": 2, "merge_info": [{"x": 1}]}},
                        },
                    ],
                },
            },
        )
    )

    first_level, blocks = await client.convert_markdown_blocks("t-ok", "# 标题\n\n| a |")

    assert first_level == ["b1", "tbl"]
    table_block = next(block for block in blocks if block["block_id"] == "tbl")
    assert "merge_info" not in table_block["table"]["property"]


@respx.mock
async def test_convert_empty_markdown_returns_nothing() -> None:
    assert await client.convert_markdown_blocks("t-ok", "  \n ") == ([], [])


@respx.mock
async def test_insert_descendants_batches_and_advances_index() -> None:
    route = respx.post(
        "https://open.feishu.cn/open-apis/docx/v1/documents/doc1/blocks/doc1/descendant"
    ).mock(return_value=httpx.Response(200, json={"code": 0, "data": {}}))

    first_level = [f"b{i}" for i in range(150)]
    descendants = [{"block_id": block_id, "block_type": 2} for block_id in first_level]
    # One nested child of b0 to prove non-first-level blocks travel with their batch.
    descendants.append({"block_id": "child-of-b0", "parent_id": "b0", "block_type": 2})

    await client.insert_descendant_blocks("t-ok", "doc1", first_level, descendants, index=3)

    assert route.call_count == 2
    first_call = route.calls[0].request
    second_call = route.calls[1].request
    import json as _json

    first_body = _json.loads(first_call.content)
    second_body = _json.loads(second_call.content)
    assert first_body["index"] == 3
    assert len(first_body["children_id"]) == 100
    assert second_body["index"] == 103
    assert len(second_body["children_id"]) == 50
    first_batch_ids = {block["block_id"] for block in first_body["descendants"]}
    assert "child-of-b0" in first_batch_ids
    assert all(block["block_id"] != "child-of-b0" for block in second_body["descendants"])


@respx.mock
async def test_delete_page_children_skips_empty_documents() -> None:
    # No route registered: any HTTP call would fail the test.
    await client.delete_page_children("t-ok", "doc1", 0)


@respx.mock
async def test_create_wiki_node_passes_parent_node_token() -> None:
    route = respx.post("https://open.feishu.cn/open-apis/wiki/v2/spaces/s1/nodes").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "node": {"node_token": "child", "obj_token": "docx-child", "obj_type": "docx"}
                },
            },
        )
    )

    node = await client.create_wiki_node(
        "t-ok", "s1", "Child", parent_node_token="wikParent"
    )

    assert node["node_token"] == "child"
    import json as _json

    assert _json.loads(route.calls[0].request.content)["parent_node_token"] == "wikParent"


@respx.mock
async def test_list_document_blocks_uses_current_revision_and_rich_blocks() -> None:
    route = respx.get("https://open.feishu.cn/open-apis/docx/v1/documents/doc1/blocks").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "items": [
                        {
                            "block_id": "b1",
                            "block_type": 3,
                            "heading1": {"elements": [{"text_run": {"content": "标题"}}]},
                        }
                    ],
                    "has_more": False,
                },
            },
        )
    )

    blocks = await client.list_document_blocks("t-ok", "doc1")

    assert blocks[0]["block_id"] == "b1"
    assert route.calls[0].request.url.params["document_revision_id"] == "-1"
    assert route.calls[0].request.url.params["page_size"] == "500"


@respx.mock
async def test_search_documents_posts_user_access_token_body() -> None:
    route = respx.post("https://open.feishu.cn/open-apis/suite/docs-api/search/object").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "docs_entities": [
                        {
                            "docs_token": "docx1",
                            "docs_type": "docx",
                            "title": "Guide",
                        }
                    ],
                    "has_more": False,
                },
            },
        )
    )

    result = await client.search_documents(
        "u-token", "docmind-mutation:abc", docs_types=["wiki", "docx"]
    )

    assert result["entities"][0]["docs_token"] == "docx1"
    import json as _json

    assert _json.loads(route.calls[0].request.content) == {
        "search_key": "docmind-mutation:abc",
        "offset": 0,
        "count": 20,
        "docs_types": ["wiki", "docx"],
    }
    assert route.calls[0].request.headers["Authorization"] == "Bearer u-token"


@respx.mock
async def test_network_errors_map_to_unavailable() -> None:
    respx.get("https://open.feishu.cn/open-apis/wiki/v2/spaces").mock(
        side_effect=httpx.ConnectError("boom")
    )

    with pytest.raises(DomainError) as caught:
        await client.list_wiki_spaces("t-ok")
    assert caught.value.code == "FEISHU_API_UNAVAILABLE"
