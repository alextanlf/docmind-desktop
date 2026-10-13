from __future__ import annotations

import asyncio
import shutil
from contextlib import suppress
from typing import cast

from fastapi import APIRouter, Request, Response, status

from app.api.documents import _remote_failure, recover_document_mutations
from app.api.errors import DomainError
from app.remote.registry import ProviderRegistry
from app.schemas.remote import CreateRemoteRepositoryRequest, RemoteRepository
from app.schemas.repositories import (
    RemovedRepositoryView,
    RepositoryCreate,
    RepositoryDelete,
    RepositoryRename,
    RepositoryRenameResult,
    RepositoryUpdate,
    RepositoryView,
)
from app.storage.models import RepositoryRecord
from app.storage.repositories import (
    DocumentMutationStore,
    ImportJobStore,
    MemoryStore,
    RemovedRemoteRepositoryStore,
    RepositoryDeletion,
    RepositoryStore,
    VectorCleanupStore,
)

router = APIRouter(prefix="/api/repositories", tags=["repositories"])


def _registry(request: Request) -> ProviderRegistry:
    return cast(ProviderRegistry, request.app.state.remote_registry)


def _store(request: Request) -> RepositoryStore:
    return cast(RepositoryStore, request.app.state.repository_store)


def _removed_store(request: Request) -> RemovedRemoteRepositoryStore:
    return cast(RemovedRemoteRepositoryStore, request.app.state.removed_repository_store)


def _view(record: RepositoryRecord, indexed_document_count: int = 0) -> RepositoryView:
    return RepositoryView(
        id=record.id,
        provider=record.provider,
        remote_id=record.remote_id,
        name=record.name,
        description=record.description,
        remote_url=record.remote_url,
        remote_parent_id=record.remote_parent_id,
        document_count=record.document_count,
        indexed_document_count=indexed_document_count,
        sync_status=record.sync_status,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _refresh_counts(request: Request, records: list[RepositoryRecord]) -> dict[str, int]:
    document_store = request.app.state.document_store
    store = _store(request)
    indexed: dict[str, int] = {}
    for record in records:
        store.set_document_count(record.id, len(document_store.list_for_repository(record.id)))
        indexed[record.id] = document_store.indexed_count_for_repository(record.id)
    return indexed


def _upsert(
    store: RepositoryStore, provider: str, remote: RemoteRepository
) -> RepositoryView:
    return _view(
        store.upsert_remote(
            provider=provider,
            remote_id=remote.remote_id,
            name=remote.name,
            description=None,
            remote_url=remote.url,
        )
    )


@router.get("", response_model=list[RepositoryView])
async def list_repositories(request: Request) -> list[RepositoryView]:
    store = _store(request)
    registry = _registry(request)
    # 用户主动移除过的远端仓库不再自动拉回来。没有这一步，"移除一个远端知识库"
    # 这个动作根本不成立 —— 下一次刷新列表它就自己回来了。
    removed = _removed_store(request).identities()
    for provider_name in registry.names():
        if not registry.is_configured(provider_name):
            continue
        provider = registry.get(provider_name)
        for remote in await provider.list_repositories():
            if (provider_name, remote.remote_id) in removed:
                continue
            _upsert(store, provider_name, remote)
    records = store.list()
    indexed = _refresh_counts(request, records)
    return [_view(record, indexed.get(record.id, 0)) for record in store.list()]


@router.post("", response_model=RepositoryView, status_code=status.HTTP_201_CREATED)
async def create_repository(request: Request, body: RepositoryCreate) -> RepositoryView:
    if body.provider:
        provider = _registry(request).get(body.provider)
        remote = await provider.create_repository(
            CreateRemoteRepositoryRequest(name=body.name)
        )
        return _upsert(_store(request), body.provider, remote)
    return _view(_store(request).create_local(name=body.name))


# 这两条静态路径必须声明在 `/{repository_id}` 之前，否则 "removed" 会被当成
# 一个仓库 id 匹配进去。
@router.get("/removed", response_model=list[RemovedRepositoryView])
async def list_removed_repositories(request: Request) -> list[RemovedRepositoryView]:
    return [
        RemovedRepositoryView(
            id=record.id,
            provider=record.provider,
            remote_id=record.remote_id,
            name=record.name,
            removed_at=record.removed_at,
        )
        for record in _removed_store(request).list()
    ]


@router.post("/removed/{removal_id}/restore", response_model=RepositoryView)
async def restore_removed_repository(request: Request, removal_id: str) -> RepositoryView:
    """恢复一个被移除的远端知识库：删掉移除记录，并当场把它拉回列表。

    当场校验"远端还在"，而不是只删记录：只删记录的话，恢复看起来成功了、列表里
    却什么都没有（远端已被删、或插件换了账号），用户无从判断发生了什么。远端确实
    不在了就如实报 404，并顺手清掉这条已经恢复不了的记录 —— 留着它只会让"已移除"
    列表永远挂着一个点不动的条目。

    文档不会随恢复回来，要用户再点「立即同步」：那是重新下载 + 重新嵌入，属于重
    操作，不该藏在一个「恢复」按钮背后。
    """
    store = _removed_store(request)
    record = store.get(removal_id)
    if record is None:
        raise DomainError("NOT_FOUND", "资源不存在", 404)
    registry = _registry(request)
    if record.provider not in registry.names() or not registry.is_configured(record.provider):
        raise DomainError("REMOTE_CREDENTIAL_REQUIRED", "请先保存该来源的访问凭据", 409, True)
    provider = registry.get(record.provider)
    remotes = await provider.list_repositories()
    remote = next((item for item in remotes if item.remote_id == record.remote_id), None)
    if remote is None:
        store.discard(removal_id)
        raise DomainError("REMOTE_NOT_FOUND", "远端知识库不存在或已不可用", 404)
    store.discard(removal_id)
    return _upsert(_store(request), record.provider, remote)


@router.patch("/{repository_id}", response_model=RepositoryView)
async def update_repository(
    request: Request, repository_id: str, body: RepositoryUpdate
) -> RepositoryView:
    store = _store(request)
    record = store.get(repository_id)
    if record is None:
        raise DomainError("NOT_FOUND", "资源不存在", 404)
    if "remote_parent_id" in body.model_fields_set:
        if not record.provider:
            raise DomainError("REMOTE_NOT_BOUND", "知识库未绑定远程来源", 409, False)
        provider = _registry(request).get(record.provider)
        if not provider.identity.capabilities.parent_node_write:
            raise DomainError(
                "REMOTE_CAPABILITY_UNSUPPORTED",
                "该远程来源不支持指定父目录",
                400,
                False,
            )
        parent_id = body.remote_parent_id.strip() if body.remote_parent_id else None
        record = store.set_remote_parent(repository_id, parent_id or None)
    return _view(record)


@router.post("/{repository_id}/rename", response_model=RepositoryRenameResult)
async def rename_repository(
    request: Request, repository_id: str, body: RepositoryRename
) -> RepositoryRenameResult:
    """给知识库改名：本地一定改；远端能改就一起改。

    单独一条路由而不是复用 ``PATCH``：那条已经把 ``remoteParentId`` 当作"唯一可改
    的东西"，而改名要如实回答**改到哪儿了** —— 本地库、以及不支持改名的 provider
    都只改本地，界面必须知道该不该提示"远端名字没跟着变"。

    provider **改不了**（没有这个能力，或当前的连接方式做不到）时只改本地并如实回
    ``local_only``：本地改名本身就是用户要的结果，报一个失败框（其实名字已经变了）
    只会骗人。远端**改了但失败**是另一回事，那种情况必须报错。
    """
    store = _store(request)
    record = store.get(repository_id)
    if record is None:
        raise DomainError("NOT_FOUND", "资源不存在", 404)
    name = body.name
    scope = "local_only"
    remote_name = name
    if record.provider and record.remote_id:
        provider = _registry(request).get(record.provider)
        if provider.identity.capabilities.repository_rename:
            try:
                remote = await provider.rename_repository(record.remote_id, name)
            except DomainError as error:
                # 能力声明与**当前生效的会话**可能对不上：语雀既可能走带 Token 的
                # API（能改名），也可能走网页会话（不能）。provider 已经如实说了
                # "这条路走不通"，那就退回"只改本地"，而不是让用户看到一个失败框
                # —— 本地改名本身已经完成了。
                if error.code != "REMOTE_CAPABILITY_UNSUPPORTED":
                    raise _remote_failure(error) from None
            except Exception as error:  # noqa: BLE001 - 网关边界统一翻译
                raise _remote_failure(error) from None
            else:
                # 用远端回给的名字，而不是我们请求的那个：远端可能规范化过（去空白、
                # 截断、替换非法字符），本地存一份和远端不一致的名字，下一秒同步就会
                # 把它改回来 —— 用户看到的是"改名自己弹回去了"。
                remote_name = remote.name or name
                scope = "remote"
    renamed = store.rename(repository_id, remote_name)
    return RepositoryRenameResult(scope=scope, repository=_view(renamed))


def _remove_document_files(request: Request, document_id: str) -> None:
    """删掉这篇文档的本地目录（正文、插图、原件副本）。

    与文档级删除同一套两步判断：目录可能是软链（旧布局），也可能是真目录。
    不跟着删的话，知识库从界面上消失了，磁盘上的正文和插图却还在。
    """
    directory = request.app.state.settings.documents_dir / document_id
    if directory.is_symlink():
        with suppress(FileNotFoundError):
            directory.unlink()
    elif directory.exists():
        with suppress(OSError):
            shutil.rmtree(directory)


async def _reject_if_busy(request: Request, repository_id: str) -> None:
    """有导入或文档变更在飞时拒绝删除。

    必须先跑一次启动恢复再检查：崩溃遗留的变更意图本来就该在这条路径上被补偿并
    删行，若不先恢复，一条陈旧记录会让知识库永远删不掉 —— 那不是保护，是卡死。
    恢复之后还留在库里的，才是此刻真正在进行的。
    """
    await recover_document_mutations(request.app)
    mutation_store = cast(DocumentMutationStore, request.app.state.document_mutation_store)
    if mutation_store.has_pending_for_repository(repository_id):
        raise DomainError("REPOSITORY_BUSY", "该知识库有文档变更正在进行", 409, True)
    import_store = cast(ImportJobStore, request.app.state.import_job_store)
    if import_store.has_active_for_repository(repository_id):
        raise DomainError("REPOSITORY_BUSY", "该知识库有导入正在进行", 409, True)


async def _drop_vectors(request: Request, repository_id: str, deletion: RepositoryDeletion) -> None:
    """清向量：仓库集合整块删，记忆集合只能逐条删。

    顺序是"先 SQLite 后向量"，与文档级删除一致 —— 仓库已经从用户眼前消失了，
    向量晚一步清掉，远好过"删了一半还看得见"。失败一律不打断请求，而是落进已有
    的清理队列，由下一次启动重放：这两条队列都是为"删不掉的东西"准备的，删除本身
    不该因为清理失败而回滚。
    """
    vector_store = request.app.state.vector_store
    try:
        await asyncio.to_thread(vector_store.delete_collection, repository_id)
    except Exception:  # noqa: BLE001 - 退回逐 id 清理，交给启动时的重放
        # 集合整删是最彻底的一条路（连没有登记在任何行里的孤儿向量一起带走），
        # 只有它失败了才退回按文档登记 ids —— 那些 ids 已在事务里带了出来。
        cleanup_store = cast(VectorCleanupStore, request.app.state.vector_cleanup_store)
        for document_id, vector_ids in deletion.document_vectors.items():
            unique_ids = list(dict.fromkeys(vector_ids))
            if unique_ids:
                cleanup_store.create(repository_id, document_id, unique_ids)

    # 记忆向量住在两个跨仓库共享的集合里，不能整块删；先落一条清理记录再删，
    # 删成功才撤记录 —— 与 memory/indexer.py 换记忆分块时同一条路径。
    memory_store = cast(MemoryStore, request.app.state.memory_store)
    for collection, vector_ids in deletion.memory_vector_ids.items():
        unique_ids = list(dict.fromkeys(vector_ids))
        if not unique_ids:
            continue
        cleanup = memory_store.create_vector_cleanup(collection, unique_ids)
        try:
            await asyncio.to_thread(vector_store.delete_memory, collection, unique_ids)
        except Exception:  # noqa: BLE001, S112 - 清理记录已经落库，留给启动重放
            continue
        if cleanup is not None:
            memory_store.delete_vector_cleanup(cleanup.id)


@router.delete("/{repository_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_repository(
    request: Request, repository_id: str, body: RepositoryDelete
) -> Response:
    """删除一个知识库：只清本地，绝不碰远端。

    远端（语雀 / 飞书）上的仓库与文档一律保留 —— 这里的语义是"从 DocMind 移除"，
    不是"把你在别处的资料删掉"。这条边界同时写在确认对话框里，用户点删除之前
    就该知道。
    """
    if not body.confirm:
        raise DomainError("CONFIRMATION_REQUIRED", "请确认删除知识库", 400)
    store = _store(request)
    if store.get(repository_id) is None:
        raise DomainError("NOT_FOUND", "资源不存在", 404)
    await _reject_if_busy(request, repository_id)
    deletion = store.delete_cascade(repository_id)
    if deletion is None:  # pragma: no cover - 上一行刚确认它存在
        raise DomainError("NOT_FOUND", "资源不存在", 404)
    for document_id in deletion.document_ids:
        _remove_document_files(request, document_id)
    await _drop_vectors(request, repository_id, deletion)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
