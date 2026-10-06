"""Low-level Feishu Open API client.

Covers the exact surface the ``FeishuProvider`` needs:

* app/tenant token acquisition (``auth/v3``),
* wiki v2 spaces and nodes (repositories and the document tree),
* docx meta / rich block listing / markdown conversion / block replacement,
* suite document search for mutation-marker lookup (user token only).

Every function takes the access token explicitly; token selection
(user OAuth token first, tenant token fallback) lives in
``app.feishu.tokens``. Feishu answers most domain errors with HTTP 200
plus a non-zero ``code`` in the envelope, so ``_request_data`` unwraps
and maps those onto ``DomainError``.
"""
from __future__ import annotations

from typing import Any

import httpx

from app.api.errors import DomainError

FEISHU_API_BASE_URL = "https://open.feishu.cn"
FEISHU_AUTHORIZE_URL = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"

_PAGE_SIZE = 50
_MAX_ITEMS = 2_000
# The descendant endpoint accepts at most 1000 blocks per call; keep the
# batch smaller to stay well under the request-body size limit.
_DESCENDANT_BATCH = 100

# Envelope codes that mean the presented token is invalid or expired.
_AUTH_ERROR_CODES = {
    99991661,  # token invalid
    99991663,  # token expired
    99991664,  # app token invalid
    99991665,  # tenant token invalid
    99991668,  # user token invalid
    99991671,  # user refresh token invalid
}
_NOT_FOUND_CODES = {1770002, 131006}  # docx not found / wiki node not found


async def _request_data(
    method: str,
    path: str,
    *,
    token: str | None = None,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
) -> Any:
    headers = {"Accept": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(
            base_url=FEISHU_API_BASE_URL,
            headers=headers,
            timeout=httpx.Timeout(20.0),
            follow_redirects=False,
        ) as client:
            response = await client.request(method, path, params=params, json=json)
    except httpx.HTTPError as error:
        raise DomainError(
            "FEISHU_API_UNAVAILABLE", "无法连接飞书开放平台，请检查网络后重试", 503, True
        ) from error

    try:
        payload = response.json()
    except ValueError as error:
        raise DomainError("FEISHU_API_ERROR", "飞书 API 返回的数据格式无效", 502, True) from error
    if not isinstance(payload, dict):
        raise DomainError("FEISHU_API_ERROR", "飞书 API 返回的数据格式无效", 502, True)

    code = payload.get("code")
    if code in (0, "0", None) and response.status_code < 400:
        # Token endpoints (auth/v3/*) put their fields at the top level
        # instead of under ``data``.
        if "data" in payload:
            return payload["data"]
        return {key: value for key, value in payload.items() if key not in {"code", "msg"}}
    if code in _AUTH_ERROR_CODES or response.status_code in {401, 403}:
        raise DomainError("FEISHU_API_AUTH_FAILED", "飞书访问凭证无效或已过期", 401, False)
    if code in _NOT_FOUND_CODES or response.status_code == 404:
        raise DomainError("FEISHU_API_NOT_FOUND", "飞书资源不存在或无访问权限", 404, False)
    message = str(payload.get("msg") or "飞书 API 请求失败")
    raise DomainError("FEISHU_API_ERROR", f"飞书 API 请求失败：{message}", 502, True)


# --- auth -----------------------------------------------------------------


async def fetch_tenant_access_token(app_id: str, app_secret: str) -> str:
    data = await _request_data(
        "POST",
        "/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
    )
    token = _extract(data, "tenant_access_token")
    if token is None:
        raise DomainError(
            "FEISHU_API_AUTH_FAILED", "飞书 App ID 或 App Secret 无效", 401, False
        )
    return token


async def fetch_app_access_token(app_id: str, app_secret: str) -> str:
    data = await _request_data(
        "POST",
        "/open-apis/auth/v3/app_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
    )
    token = _extract(data, "app_access_token")
    if token is None:
        raise DomainError(
            "FEISHU_API_AUTH_FAILED", "飞书 App ID 或 App Secret 无效", 401, False
        )
    return token


async def exchange_user_access_token(app_access_token: str, code: str) -> dict[str, Any]:
    data = await _request_data(
        "POST",
        "/open-apis/authen/v1/oidc/access_token",
        token=app_access_token,
        json={"grant_type": "authorization_code", "code": code},
    )
    if not isinstance(data, dict) or not data.get("access_token"):
        raise DomainError(
            "FEISHU_OAUTH_FAILED", "飞书授权码交换失败，请重新授权", 401, False, "重新点击授权"
        )
    return data


async def refresh_user_access_token(app_access_token: str, refresh_token: str) -> dict[str, Any]:
    data = await _request_data(
        "POST",
        "/open-apis/authen/v1/oidc/refresh_access_token",
        token=app_access_token,
        json={"grant_type": "refresh_token", "refresh_token": refresh_token},
    )
    if not isinstance(data, dict) or not data.get("access_token"):
        raise DomainError(
            "FEISHU_OAUTH_FAILED", "飞书用户授权已失效，请重新授权", 401, False, "重新点击授权"
        )
    return data


async def fetch_user_name(user_access_token: str) -> str:
    data = await _request_data(
        "GET", "/open-apis/authen/v1/user_info", token=user_access_token
    )
    if not isinstance(data, dict):
        raise DomainError("FEISHU_API_ERROR", "飞书 API 返回的用户数据无效", 502, True)
    return str(data.get("name") or "")


# --- wiki (repositories + document tree) -----------------------------------


async def list_wiki_spaces(token: str) -> list[dict[str, Any]]:
    return await _list_paged(token, "/open-apis/wiki/v2/spaces")


async def create_wiki_space(token: str, name: str) -> dict[str, Any]:
    data = await _request_data(
        "POST", "/open-apis/wiki/v2/spaces", token=token, json={"name": name}
    )
    space = _extract(data, "space")
    if not isinstance(space, dict) or not space.get("space_id"):
        raise DomainError("FEISHU_API_ERROR", "飞书 API 返回的知识库数据无效", 502, True)
    return space


async def list_space_nodes(
    token: str, space_id: str, parent_node_token: str | None = None
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}
    if parent_node_token:
        params["parent_node_token"] = parent_node_token
    return await _list_paged(token, f"/open-apis/wiki/v2/spaces/{space_id}/nodes", params=params)


async def get_wiki_node(
    token: str, node_token: str, obj_type: str = "wiki"
) -> dict[str, Any]:
    data = await _request_data(
        "GET",
        "/open-apis/wiki/v2/spaces/get_node",
        token=token,
        params={"token": node_token, "obj_type": obj_type},
    )
    node = _extract(data, "node")
    if not isinstance(node, dict) or not node.get("node_token"):
        raise DomainError("FEISHU_API_NOT_FOUND", "飞书知识库节点不存在", 404, False)
    return node


async def create_wiki_node(
    token: str,
    space_id: str,
    title: str,
    parent_node_token: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"obj_type": "docx", "node_type": "origin", "title": title}
    if parent_node_token:
        body["parent_node_token"] = parent_node_token
    data = await _request_data(
        "POST",
        f"/open-apis/wiki/v2/spaces/{space_id}/nodes",
        token=token,
        json=body,
    )
    node = _extract(data, "node")
    if not isinstance(node, dict) or not node.get("node_token"):
        raise DomainError("FEISHU_API_ERROR", "飞书 API 返回的节点数据无效", 502, True)
    return node


async def update_wiki_node_title(token: str, space_id: str, node_token: str, title: str) -> None:
    await _request_data(
        "POST",
        f"/open-apis/wiki/v2/spaces/{space_id}/nodes/{node_token}/update_title",
        token=token,
        json={"title": title},
    )


# --- docx (document content) -------------------------------------------------


async def fetch_document_meta(token: str, document_id: str) -> dict[str, Any]:
    data = await _request_data(
        "GET", f"/open-apis/docx/v1/documents/{document_id}", token=token
    )
    document = _extract(data, "document")
    if not isinstance(document, dict):
        raise DomainError("FEISHU_API_NOT_FOUND", "飞书文档不存在", 404, False)
    return document


async def fetch_document_raw_content(token: str, document_id: str) -> str:
    data = await _request_data(
        "GET", f"/open-apis/docx/v1/documents/{document_id}/raw_content", token=token
    )
    content = _extract(data, "content")
    return content if isinstance(content, str) else ""


async def list_document_blocks(token: str, document_id: str) -> list[dict[str, Any]]:
    """Return every rich-text block in one docx document."""
    return await _list_paged(
        token,
        f"/open-apis/docx/v1/documents/{document_id}/blocks",
        params={"page_size": 500, "document_revision_id": -1},
    )


async def search_documents(
    token: str,
    search_key: str,
    *,
    offset: int = 0,
    count: int = 20,
    docs_types: list[str] | None = None,
) -> dict[str, Any]:
    """Search cloud documents with a user access token.

    The suite search endpoint only accepts user tokens.  Callers must fall
    back to a repository walk when the user channel is unavailable.
    """
    body: dict[str, Any] = {"search_key": search_key, "offset": offset, "count": count}
    if docs_types:
        body["docs_types"] = docs_types
    data = await _request_data(
        "POST",
        "/open-apis/suite/docs-api/search/object",
        token=token,
        json=body,
    )
    if not isinstance(data, dict):
        raise DomainError("FEISHU_API_ERROR", "飞书搜索返回的数据格式无效", 502, True)
    entities = [item for item in (data.get("docs_entities") or []) if isinstance(item, dict)]
    return {"entities": entities, "has_more": bool(data.get("has_more"))}


async def convert_markdown_blocks(token: str, markdown: str) -> tuple[list[str], list[dict[str, Any]]]:
    """Convert markdown into docx blocks via the official convert endpoint.

    Returns ``(first_level_block_ids, descendants)`` ready for the
    descendant insert endpoint. Table ``merge_info`` is stripped because it
    is read-only and rejected by the insert call.
    """
    if not markdown.strip():
        return [], []
    data = await _request_data(
        "POST",
        "/open-apis/docx/v1/documents/blocks/convert",
        token=token,
        json={"content_type": "markdown", "content": markdown},
    )
    if not isinstance(data, dict):
        raise DomainError("FEISHU_API_ERROR", "飞书 Markdown 转换结果无效", 502, True)
    first_level = data.get("first_level_block_ids") or []
    blocks = data.get("blocks") or []
    cleaned: list[dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        table = block.get("table")
        if isinstance(table, dict):
            prop = table.get("property")
            if isinstance(prop, dict):
                prop.pop("merge_info", None)
        cleaned.append(block)
    return [str(item) for item in first_level], cleaned


async def list_page_children(token: str, document_id: str) -> list[dict[str, Any]]:
    return await _list_paged(
        token, f"/open-apis/docx/v1/documents/{document_id}/blocks/{document_id}/children"
    )


async def delete_page_children(token: str, document_id: str, count: int) -> None:
    if count <= 0:
        return
    await _request_data(
        "DELETE",
        f"/open-apis/docx/v1/documents/{document_id}/blocks/{document_id}/children/batch_delete",
        token=token,
        json={"start_index": 0, "end_index": count},
    )


async def insert_descendant_blocks(
    token: str,
    document_id: str,
    first_level_ids: list[str],
    descendants: list[dict[str, Any]],
    *,
    index: int = 0,
) -> None:
    if not descendants:
        return
    cursor = index
    for offset in range(0, len(first_level_ids), _DESCENDANT_BATCH):
        batch_ids = first_level_ids[offset : offset + _DESCENDANT_BATCH]
        batch = _descendants_for_batch(descendants, batch_ids)
        await _request_data(
            "POST",
            f"/open-apis/docx/v1/documents/{document_id}/blocks/{document_id}/descendant",
            token=token,
            json={"index": cursor, "children_id": batch_ids, "descendants": batch},
        )
        cursor += len(batch_ids)


async def delete_drive_file(token: str, file_token: str, file_type: str = "docx") -> None:
    await _request_data(
        "DELETE",
        f"/open-apis/drive/v1/files/{file_token}",
        token=token,
        params={"type": file_type},
    )


# --- helpers -----------------------------------------------------------------


def _descendants_for_batch(
    descendants: list[dict[str, Any]],
    batch_ids: list[str],
) -> list[dict[str, Any]]:
    """Collect the descendants belonging to one first-level batch.

    A descendant belongs to the batch when it is one of the batch blocks or
    its (transitive) parent is. Block ids are caller-assigned in the convert
    response, so membership can be resolved through the ``parent_id`` links.

    🔴 此前还有第三个 `first_level_id_set` 参数，函数体内从未读取
    （成员判定只用 `included`，即本批次的 batch_ids），
    调用方还为此每批重建一次 `set(first_level_ids)`。已删除。
    """
    included = set(batch_ids)
    changed = True
    while changed:
        changed = False
        for block in descendants:
            block_id = block.get("block_id")
            parent_id = block.get("parent_id")
            # Kept nested on purpose: the outer check admits a block whose parent
            # is already included, the inner one guards against adding it twice.
            # Collapsing them would skip the `not in included` test.
            if block_id in included or parent_id in included:  # noqa: SIM102
                if block_id not in included:
                    included.add(block_id)
                    changed = True
    return [block for block in descendants if block.get("block_id") in included]


async def _list_paged(
    token: str, path: str, *, params: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page_token: str | None = None
    while len(items) < _MAX_ITEMS:
        query: dict[str, Any] = {"page_size": _PAGE_SIZE, **(params or {})}
        if page_token:
            query["page_token"] = page_token
        data = await _request_data("GET", path, token=token, params=query)
        if not isinstance(data, dict):
            raise DomainError("FEISHU_API_ERROR", "飞书 API 返回的列表数据无效", 502, True)
        rows = data.get("items") or []
        items.extend(row for row in rows if isinstance(row, dict))
        if not data.get("has_more"):
            break
        page_token = data.get("page_token") or None
        if page_token is None:
            break
    return items[:_MAX_ITEMS]


def _extract(data: Any, key: str) -> Any:
    if isinstance(data, dict):
        return data.get(key)
    return None
