"""Feishu (飞书文档) remote provider.

Mapping onto the ``RemoteProvider`` contract:

* repository ↔ wiki space (``remote_id`` = ``space_id``)
* document   ↔ wiki node of type ``docx`` (``remote_id`` = ``node_token``);
  the underlying docx object (``obj_token``) is resolved per operation

Auth strategy: the OAuth user token (``user`` channel) is preferred, the
self-built app's tenant token (``app`` channel) is the fallback — see
``app.feishu.tokens``. Content writes go through the official
markdown → blocks convert endpoint plus descendant insertion, so no
hand-written block model is needed. Reads use the docx ``raw_content``
endpoint and therefore return plain text rather than markdown (a v1
trade-off, same shape as the Yuque browser fallback).

Known limits: only ``docx`` nodes are listed (legacy ``doc`` and
spreadsheet/bitable nodes are skipped); list indentation is flattened by
the wiki tree walk.
"""
from __future__ import annotations

from app.api.errors import DomainError
from app.feishu import client
from app.feishu.oauth import FeishuOAuthFlow
from app.feishu.tokens import FeishuTokenManager
from app.remote.provider import ProviderCapabilities, ProviderIdentity
from app.schemas.remote import (
    CreateRemoteDocumentRequest,
    CreateRemoteRepositoryRequest,
    LoginResult,
    LoginStatus,
    RemoteDocument,
    RemoteDocumentContent,
    RemoteRepository,
    UpdateRemoteDocumentRequest,
)

_MAX_TREE_DEPTH = 16


class FeishuProvider:
    identity = ProviderIdentity(
        name="feishu",
        label="飞书文档",
        capabilities=ProviderCapabilities(browser_install=False, marker_lookup=True),
    )

    def __init__(
        self,
        token_manager: FeishuTokenManager,
        oauth_flow: FeishuOAuthFlow | None = None,
    ) -> None:
        self._tokens = token_manager
        self._oauth = oauth_flow or FeishuOAuthFlow()

    # -- login surface (the ``user`` channel) ---------------------------------

    async def login_status(self) -> LoginStatus:
        record = self._tokens.credential_store.get("feishu", "user")
        bundle = self._tokens.user_bundle()
        if record is not None and record.state == "verified" and bundle is not None:
            token = await self._tokens.user_token()
            if token is not None:
                label = bundle.name
                if not label:
                    try:
                        label = await client.fetch_user_name(token)
                    except DomainError:
                        label = None
                return LoginStatus(
                    logged_in=True,
                    account_label=_mask_account(label or ""),
                    requires_login=False,
                )
        return LoginStatus(logged_in=False, account_label=None, requires_login=True)

    async def begin_login(self) -> LoginResult:
        credentials = self._tokens.app_credentials()
        if credentials is None:
            raise DomainError(
                "FEISHU_APP_CREDENTIALS_REQUIRED",
                "请先保存飞书自建应用凭据（App ID:App Secret），再完成账号授权",
                400,
                False,
                "先在下方保存飞书自建应用凭据",
            )
        bundle = await self._oauth.run(*credentials)
        self._tokens.save_user_bundle(bundle)
        self._tokens.credential_store.mark_state(
            "feishu", "user", "verified", account_label=bundle.name
        )
        return LoginResult(
            logged_in=True,
            account_label=_mask_account(bundle.name or ""),
            requires_login=False,
        )

    # -- repositories (wiki spaces) --------------------------------------------

    async def list_repositories(self) -> list[RemoteRepository]:
        token = await self._tokens.require_token()
        spaces = await client.list_wiki_spaces(token)
        return [
            RemoteRepository(
                remote_id=str(space["space_id"]),
                name=str(space.get("name") or space["space_id"]),
                url=None,
            )
            for space in spaces
            if space.get("space_id")
        ]

    async def create_repository(self, request: CreateRemoteRepositoryRequest) -> RemoteRepository:
        token = await self._tokens.require_token()
        space = await client.create_wiki_space(token, request.name)
        return RemoteRepository(
            remote_id=str(space["space_id"]),
            name=str(space.get("name") or request.name),
            url=None,
        )

    # -- documents (wiki docx nodes) --------------------------------------------

    async def list_documents(self, repository_id: str) -> list[RemoteDocument]:
        token = await self._tokens.require_token()
        documents: list[RemoteDocument] = []
        # Iterative depth-first walk over the wiki node tree.
        stack: list[tuple[str | None, int]] = [(None, 0)]
        while stack:
            parent_token, depth = stack.pop()
            if depth > _MAX_TREE_DEPTH:
                continue
            nodes = await client.list_space_nodes(token, repository_id, parent_token)
            for node in reversed(nodes):
                if node.get("obj_type") != "docx":
                    # Still descend: containers may hold docx children.
                    if node.get("has_child") and node.get("node_token"):
                        stack.append((str(node["node_token"]), depth + 1))
                    continue
                node_token = node.get("node_token")
                if not node_token:
                    continue
                documents.append(
                    RemoteDocument(
                        remote_id=str(node_token),
                        repository_id=repository_id,
                        title=str(node.get("title") or node_token),
                        url=f"https://feishu.cn/wiki/{node_token}",
                    )
                )
                if node.get("has_child"):
                    stack.append((str(node_token), depth + 1))
        return documents

    async def create_document(self, request: CreateRemoteDocumentRequest) -> RemoteDocument:
        token = await self._tokens.require_token()
        node = await client.create_wiki_node(token, request.repository_id, request.title)
        node_token = str(node["node_token"])
        document_id = str(node.get("obj_token") or "")
        if request.content.strip() and document_id:
            await _replace_document_body(token, document_id, request.content)
        return RemoteDocument(
            remote_id=node_token,
            repository_id=request.repository_id,
            title=request.title,
            url=f"https://feishu.cn/wiki/{node_token}",
        )

    async def find_document_by_marker(
        self, repository_id: str, marker: str
    ) -> RemoteDocument | None:
        for document in await self.list_documents(repository_id):
            content = await self.read_document(document.remote_id)
            if marker in content.content:
                return document
        return None

    async def document_exists(self, repository_id: str, document_id: str) -> bool:
        token = await self._tokens.require_token()
        try:
            node = await client.get_wiki_node(token, document_id)
        except DomainError as error:
            if error.code in {"FEISHU_API_NOT_FOUND", "FEISHU_API_AUTH_FAILED"}:
                return False
            raise
        return str(node.get("space_id") or "") == repository_id

    async def read_document(self, document_id: str) -> RemoteDocumentContent:
        token = await self._tokens.require_token()
        node = await client.get_wiki_node(token, document_id)
        obj_token = str(node.get("obj_token") or "")
        if not obj_token:
            raise DomainError("FEISHU_API_ERROR", "飞书节点缺少文档标识", 502, True)
        content = await client.fetch_document_raw_content(token, obj_token)
        title = str(node.get("title") or document_id)
        return RemoteDocumentContent(
            remote_id=document_id,
            repository_id=str(node.get("space_id") or ""),
            title=title,
            content=content,
            url=f"https://feishu.cn/wiki/{document_id}",
        )

    async def update_document(self, request: UpdateRemoteDocumentRequest) -> RemoteDocument:
        token = await self._tokens.require_token()
        node = await client.get_wiki_node(token, request.document_id)
        obj_token = str(node.get("obj_token") or "")
        space_id = str(node.get("space_id") or "")
        if not obj_token:
            raise DomainError("FEISHU_API_ERROR", "飞书节点缺少文档标识", 502, True)
        await _replace_document_body(token, obj_token, request.content)
        if space_id and request.title != str(node.get("title") or ""):
            await client.update_wiki_node_title(
                token, space_id, request.document_id, request.title
            )
        return RemoteDocument(
            remote_id=request.document_id,
            repository_id=space_id,
            title=request.title,
            url=f"https://feishu.cn/wiki/{request.document_id}",
        )

    async def delete_document(self, document_id: str, repository_id: str) -> None:
        token = await self._tokens.require_token()
        node = await client.get_wiki_node(token, document_id)
        obj_token = str(node.get("obj_token") or "")
        if not obj_token:
            raise DomainError("FEISHU_API_ERROR", "飞书节点缺少文档标识", 502, True)
        await client.delete_drive_file(token, obj_token, "docx")

    async def close(self) -> None:
        return None


async def _replace_document_body(token: str, document_id: str, markdown: str) -> None:
    """Replace the whole page body: convert → clear → insert."""
    first_level_ids, descendants = await client.convert_markdown_blocks(token, markdown)
    children = await client.list_page_children(token, document_id)
    await client.delete_page_children(token, document_id, len(children))
    await client.insert_descendant_blocks(token, document_id, first_level_ids, descendants)


def _mask_account(value: str) -> str | None:
    compact = value.strip()
    if not compact:
        return None
    if len(compact) <= 2:
        return "*" * len(compact)
    return f"{compact[0]}***{compact[-1]}"
