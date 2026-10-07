"""Feishu (飞书文档) remote provider.

Mapping onto the ``RemoteProvider`` contract:

* repository ↔ wiki space (``remote_id`` = ``space_id``)
* document   ↔ wiki node of type ``docx`` (``remote_id`` = ``node_token``);
  the underlying docx object (``obj_token``) is resolved per operation

Auth strategy: the OAuth user token (``user`` channel) is preferred, the
self-built app's tenant token (``app`` channel) is the fallback — see
``app.feishu.tokens``. Content writes go through the official
markdown → blocks convert endpoint plus descendant insertion, so no
hand-written block model is needed. Reads list the docx blocks and rebuild
common Markdown structures (headings, lists, code, quotes, todos and
tables); rich blocks without a Markdown equivalent degrade to explicit
placeholders. New documents can target a configured wiki ``parent_node_token``.

Known limits: only ``docx`` nodes are listed (legacy ``doc`` and
spreadsheet/bitable nodes are skipped).
"""
from __future__ import annotations

from app.api.errors import DomainError
from app.feishu import client
from app.feishu.markdown import render_document_blocks
from app.feishu.oauth import FeishuOAuthFlow
from app.feishu.tokens import FeishuTokenManager
from app.remote.markers import strip_mutation_marker
from app.remote.provider import ProviderCapabilities, ProviderIdentity
from app.remote.redaction import mask_account
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
        summary="把飞书云文档导入 DocMind",
        icon="library",
        keywords=("feishu", "飞书", "lark", "云文档"),
        homepage="https://open.feishu.cn/document/",
        capabilities=ProviderCapabilities(
            browser_install=False,
            marker_lookup=True,
            parent_node_write=True,
        ),
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
                    account_label=mask_account(label or ""),
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
            account_label=mask_account(bundle.name or ""),
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
        return [
            _node_to_document(node, repository_id)
            for node in await self._walk_nodes(token, repository_id)
            if str(node.get("obj_type") or "") == "docx"
        ]

    async def create_document(self, request: CreateRemoteDocumentRequest) -> RemoteDocument:
        token = await self._tokens.require_token()
        node = await client.create_wiki_node(
            token,
            request.repository_id,
            request.title,
            parent_node_token=request.parent_id,
        )
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
        # Suite search requires a user token; tenant-only installations keep
        # the correctness-preserving repository walk as a fallback.
        user_token = await self._tokens.user_token()
        if user_token:
            try:
                found = await self._find_document_by_search(user_token, repository_id, marker)
                if found is not None:
                    return found
            except DomainError:
                # Search is an optimization, never a correctness dependency.
                pass
        return await self._find_document_by_walk(repository_id, marker)

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
        return await self._read_document(document_id, strip_marker=True)

    async def _read_document(
        self, document_id: str, *, strip_marker: bool
    ) -> RemoteDocumentContent:
        token = await self._tokens.require_token()
        node = await client.get_wiki_node(token, document_id)
        obj_token = str(node.get("obj_token") or "")
        if not obj_token:
            raise DomainError("FEISHU_API_ERROR", "飞书节点缺少文档标识", 502, True)
        blocks = await client.list_document_blocks(token, obj_token)
        content = render_document_blocks(blocks)
        if strip_marker:
            content = strip_mutation_marker(content)
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

    async def _walk_nodes(self, token: str, repository_id: str) -> list[dict[str, object]]:
        """Walk wiki metadata without reading document bodies."""
        nodes: list[dict[str, object]] = []
        stack: list[tuple[str | None, int]] = [(None, 0)]
        while stack:
            parent_token, depth = stack.pop()
            if depth > _MAX_TREE_DEPTH:
                continue
            children = await client.list_space_nodes(token, repository_id, parent_token)
            for node in reversed(children):
                nodes.append(node)
                node_token = node.get("node_token")
                if node.get("has_child") and node_token:
                    stack.append((str(node_token), depth + 1))
        return nodes

    async def _find_document_by_search(
        self, user_token: str, repository_id: str, marker: str
    ) -> RemoteDocument | None:
        offset = 0
        scanned = 0
        while scanned < 200:
            page = await client.search_documents(
                user_token,
                marker,
                offset=offset,
                count=20,
                docs_types=["wiki", "docx"],
            )
            entities = page["entities"]
            if not entities:
                break
            for entity in entities:
                document = await self._node_for_search_entity(
                    user_token, repository_id, entity
                )
                if document is None:
                    continue
                content = await self._read_document(
                    document.remote_id, strip_marker=False
                )
                if marker in content.content:
                    return document
            scanned += len(entities)
            if not page["has_more"]:
                break
            offset += len(entities)
        return None

    async def _node_for_search_entity(
        self, user_token: str, repository_id: str, entity: dict[str, object]
    ) -> RemoteDocument | None:
        token = str(entity.get("docs_token") or "")
        docs_type = str(entity.get("docs_type") or "wiki")
        if not token:
            return None
        try:
            node = await client.get_wiki_node(user_token, token, obj_type=docs_type)
        except DomainError as error:
            if error.code == "FEISHU_API_NOT_FOUND":
                return None
            raise
        if str(node.get("space_id") or "") != repository_id:
            return None
        if str(node.get("obj_type") or docs_type) != "docx":
            return None
        return _node_to_document(node, repository_id)

    async def _find_document_by_walk(
        self, repository_id: str, marker: str
    ) -> RemoteDocument | None:
        for document in await self.list_documents(repository_id):
            content = await self._read_document(document.remote_id, strip_marker=False)
            if marker in content.content:
                return document
        return None


async def _replace_document_body(token: str, document_id: str, markdown: str) -> None:
    """Replace the whole page body: convert → clear → insert."""
    first_level_ids, descendants = await client.convert_markdown_blocks(token, markdown)
    children = await client.list_page_children(token, document_id)
    await client.delete_page_children(token, document_id, len(children))
    await client.insert_descendant_blocks(token, document_id, first_level_ids, descendants)


def _node_to_document(node: dict[str, object], repository_id: str) -> RemoteDocument:
    node_token = str(node.get("node_token") or "")
    return RemoteDocument(
        remote_id=node_token,
        repository_id=repository_id,
        title=str(node.get("title") or node_token),
        url=f"https://feishu.cn/wiki/{node_token}",
    )
