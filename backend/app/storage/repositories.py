from __future__ import annotations

import base64
import inspect
import json
from collections.abc import Callable, Iterable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, delete, event, func, or_, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.imports.batch_state_machine import ensure_item_mutable, transition
from app.imports.state_machine import ensure_transition_allowed
from app.schemas.batches import BatchItemPage, BatchItemView, ConfirmBatchInput
from app.schemas.graph import GraphEdge, GraphNode
from app.schemas.sync import RemoteDocumentState
from app.schemas.versioning import DocumentVersion
from app.storage.database import Database
from app.storage.models import (
    BatchImportRecord,
    BatchItemDecision,
    BatchItemRecord,
    BatchItemState,
    BatchState,
    ChatRequestRecord,
    CrawlEntryRecord,
    CrawlEntryState,
    CrawlState,
    DistillationRecord,
    DocumentChunkRecord,
    DocumentMutationRecord,
    DocumentRecord,
    DocumentVersionRecord,
    EmbeddingRebuildRecord,
    GraphEdgeRecord,
    GraphNodeRecord,
    ImportJobRecord,
    ImportStatus,
    MemoryChunkRecord,
    MemoryVectorCleanupRecord,
    MessageRecord,
    RemovedRemoteDocumentRecord,
    RemovedRemoteRepositoryRecord,
    RepositoryRecord,
    RepositorySyncMetaRecord,
    RepositorySyncStateRecord,
    SessionRecord,
    SessionSummaryRecord,
    SettingRecord,
    VectorCleanupRecord,
    WebCorpusChunkRecord,
    WebCorpusPageRecord,
    WebSearchResultRecord,
    WebSearchRunRecord,
    WebSourceRecord,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _stale_confirmation(message: str = "批次来源或目标已变化，请重新确认") -> DomainError:
    return DomainError("BATCH_STALE_CONFIRMATION", message, 409, False)


def _prune_scope(raw: str | None, repository_id: str) -> str | None:
    """从 JSON 数组里摘掉一个仓库 id；没命中就返回 ``None``（表示不必写回）。

    这些范围不是"显示用"的副本，而是索引侧真正会读的输入：``memory/indexer.py``
    拿 ``session_summaries.repository_ids_json`` 去建 memory_chunks，而那一列的
    ``repository_id`` 是指向 repositories 的外键。留下悬空 id 的后果不是界面显示
    得不对，是下次索引直接撞外键约束 —— 一个永远重试不成功的记忆摘要。
    """
    try:
        loaded = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(loaded, list):
        return None
    kept = [item for item in loaded if item != repository_id]
    if len(kept) == len(loaded):
        return None
    return json.dumps(kept)


@dataclass(frozen=True)
class RepositoryDeletion:
    """一次知识库删除之后，留在别处的东西。

    SQLite 侧是一次事务，但向量不在这张库里：文档分块住在按仓库命名的集合，记忆
    分块住在两个共享集合。删集合是会失败的外部操作，必须还能重试，所以这里把
    「还欠哪些向量」原样带出去，由调用方当场删或落进已有的清理队列 —— 与文档级
    删除走同一条路径，而不是在这里偷偷起一个没人重放的清理流程。
    """

    document_ids: list[str]
    #: document_id → 那个文档的向量 id（含之前删除失败、还挂在清理队列里的那些）。
    #: 集合整删成功就一行都用不上；整删失败时靠它回退。
    document_vectors: dict[str, list[str]]
    memory_vector_ids: dict[str, list[str]]


_batch_confirmation_session: ContextVar[Session | None] = ContextVar(
    "batch_confirmation_session", default=None
)


class RepositoryStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list(self) -> list[RepositoryRecord]:
        with self.database.session() as session:
            return list(
                session.scalars(
                    select(RepositoryRecord).order_by(RepositoryRecord.created_at.desc())
                )
            )

    def get(self, repository_id: str) -> RepositoryRecord | None:
        with self.database.session() as session:
            return session.get(RepositoryRecord, repository_id)

    def create_local(self, *, name: str, description: str | None = None) -> RepositoryRecord:
        with self.database.session() as session:
            record = RepositoryRecord(
                name=name,
                description=description,
                provider=None,
                remote_id=None,
                remote_url=None,
                sync_status="local",
            )
            session.add(record)
            session.flush()
            return record

    def set_document_count(self, repository_id: str, count: int) -> None:
        with self.database.session() as session:
            record = session.get(RepositoryRecord, repository_id)
            if record is not None:
                record.document_count = count
                record.updated_at = utc_now()

    def set_remote_parent(self, repository_id: str, parent_id: str | None) -> RepositoryRecord:
        with self.database.session() as session:
            record = session.get(RepositoryRecord, repository_id)
            if record is None:
                raise DomainError("NOT_FOUND", "资源不存在", 404)
            record.remote_parent_id = parent_id
            record.updated_at = utc_now()
            session.flush()
            session.expunge(record)
            return record

    def upsert_remote(
        self,
        *,
        provider: str,
        remote_id: str,
        name: str,
        description: str | None,
        remote_url: str | None,
    ) -> RepositoryRecord:
        with self.database.session() as session:
            record = session.scalar(
                select(RepositoryRecord).where(
                    RepositoryRecord.provider == provider,
                    RepositoryRecord.remote_id == remote_id,
                )
            )
            if record is None:
                record = RepositoryRecord(
                    provider=provider,
                    remote_id=remote_id,
                    name=name,
                    description=description,
                    remote_url=remote_url,
                )
                session.add(record)
            else:
                (
                    record.provider,
                    record.name,
                    record.description,
                    record.remote_url,
                    record.updated_at,
                ) = (
                    provider,
                    name,
                    description,
                    remote_url,
                    utc_now(),
                )
            session.flush()
            return record

    def delete_cascade(self, repository_id: str) -> RepositoryDeletion | None:
        """删掉一个知识库，以及所有以它为锚的本地状态；不存在时返回 ``None``。

        外键兜得住的交给 SQLite：``documents`` / ``document_chunks`` /
        ``memory_chunks`` 级联删除，``import_jobs`` / ``batch_imports`` /
        ``distillations.target_repository_id`` 置空。下面三类外键兜不住，必须
        显式处理 —— 它们只存了一个 id 字符串，schema 里根本没有 REFERENCES：

        * **裸 repository_id 列**：``vector_cleanups`` / ``document_mutations`` /
          ``repository_sync_state`` / ``repository_sync_meta``。
        * **按 document_id 挂的衍生数据**：``document_versions`` /
          ``graph_nodes`` / ``graph_edges`` / ``embedding_rebuild``。不删它们，
          图谱会继续列着已删文档的节点，而那正是用户以为"删干净了"的地方。
        * **JSON 里的仓库范围**：会话范围、记忆摘要与蒸馏的范围（见
          ``_prune_scope``：那里留着悬空 id 会让下次索引撞外键）。

        向量一概不在这里删 —— 见 ``RepositoryDeletion``。
        """
        with self.database.session() as session:
            record = session.get(RepositoryRecord, repository_id)
            if record is None:
                return None

            # 只取 id 列而不是实体：一旦 DocumentRecord 进了 identity map，
            # SQLAlchemy 就不会再对 ``passive_deletes`` 视而不见，而会去把子行的
            # 外键置空 —— 那正好和外键的 ON DELETE CASCADE 打架。
            document_ids = list(
                session.scalars(
                    select(DocumentRecord.id).where(DocumentRecord.repository_id == repository_id)
                )
            )
            memory_vector_ids: dict[str, list[str]] = {}
            # 函数内导入是必须的，不是随手：`app.memory.collections` 会先执行
            # `app/memory/__init__.py`，它反过来 import 了 app.memory.summary，
            # 而 summary 又 import 本模块 —— 模块级导入会在这里炸成循环。
            from app.memory.collections import DISTILLATION_COLLECTION, SUMMARY_COLLECTION

            for vector_id, summary_id in session.execute(
                select(MemoryChunkRecord.vector_id, MemoryChunkRecord.summary_id).where(
                    MemoryChunkRecord.repository_id == repository_id
                )
            ):
                collection = (
                    SUMMARY_COLLECTION if summary_id is not None else DISTILLATION_COLLECTION
                )
                memory_vector_ids.setdefault(collection, []).append(vector_id)

            document_vectors: dict[str, list[str]] = {}
            for document_id, vector_id in session.execute(
                select(DocumentChunkRecord.document_id, DocumentChunkRecord.vector_id).where(
                    DocumentChunkRecord.repository_id == repository_id,
                    DocumentChunkRecord.vector_id.is_not(None),
                )
            ):
                document_vectors.setdefault(document_id, []).append(vector_id)
            # 上一次文档删除没删掉的向量还挂在清理队列里，而队列行马上就要被下面
            # 那轮 delete 带走。先把 ids 接过来，否则集合整删一旦失败就没人再记得
            # 它们 —— 那才是真正的泄漏：行没了、向量还在，且再也没人查得到。
            for document_id, raw in session.execute(
                select(VectorCleanupRecord.document_id, VectorCleanupRecord.vector_ids_json).where(
                    VectorCleanupRecord.repository_id == repository_id
                )
            ):
                document_vectors.setdefault(document_id, []).extend(json.loads(raw or "[]"))

            for model in (
                VectorCleanupRecord,
                DocumentMutationRecord,
                RepositorySyncStateRecord,
                RepositorySyncMetaRecord,
            ):
                session.execute(delete(model).where(model.repository_id == repository_id))

            if document_ids:
                for model in (DocumentVersionRecord, EmbeddingRebuildRecord):
                    session.execute(delete(model).where(model.document_id.in_(document_ids)))
                # 图的边按节点 id 挂，两个方向都要摘；漏掉 target 方向会留下
                # 指向已删文档节点的悬空边。
                for column in (GraphEdgeRecord.source_id, GraphEdgeRecord.target_id):
                    session.execute(delete(GraphEdgeRecord).where(column.in_(document_ids)))
                session.execute(
                    delete(GraphNodeRecord).where(GraphNodeRecord.document_id.in_(document_ids))
                )

            for model, column in (
                (SessionRecord, SessionRecord.repository_scope_json),
                (SessionSummaryRecord, SessionSummaryRecord.repository_ids_json),
                (DistillationRecord, DistillationRecord.repository_ids_json),
            ):
                self._prune_scope_column(session, model, column, repository_id)

            session.delete(record)
            # "已移除"必须和删除在同一个事务里写。分两次写会出现两种半状态：
            # 删掉了但没记住（下次刷新它自己回来），或记住了但没删掉（界面里没了、
            # 数据还在）。远端绑定的库才需要 —— 只有它会被列表同步重新拉进来。
            if record.provider and record.remote_id:
                session.execute(
                    sqlite_insert(RemovedRemoteRepositoryRecord)
                    .values(
                        id=str(uuid4()),
                        provider=record.provider,
                        remote_id=record.remote_id,
                        name=record.name,
                    )
                    .on_conflict_do_update(
                        index_elements=[
                            RemovedRemoteRepositoryRecord.provider,
                            RemovedRemoteRepositoryRecord.remote_id,
                        ],
                        set_={"name": record.name},
                    )
                )
            session.flush()
            return RepositoryDeletion(
                document_ids=document_ids,
                document_vectors=document_vectors,
                memory_vector_ids=memory_vector_ids,
            )

    @staticmethod
    def _prune_scope_column(session: Session, model: Any, column: Any, repository_id: str) -> None:
        """把 ``column`` 里的仓库 id 摘掉。

        按 (id, 列) 逐行读，而不是把整行实体取回来：``session_summaries`` 与
        ``distillations`` 都有 ``content`` 正文列，为了改一行范围把全文读进内存
        不值当。
        """
        for row_id, raw in session.execute(select(model.id, column)):
            pruned = _prune_scope(raw, repository_id)
            if pruned is not None:
                session.execute(
                    update(model).where(model.id == row_id).values({column.key: pruned})
                )


class RemovedRemoteRepositoryStore:
    """「用户主动移除的远端知识库」这张表的读写。

    条目只在两处产生/消失：删除远端绑定的知识库时写下（在 ``RepositoryStore.
    delete_cascade`` 的同一个事务里），用户点「恢复」时删掉。列表同步读它来跳过
    已被移除的远端仓库。
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    def list(self) -> list[RemovedRemoteRepositoryRecord]:
        with self.database.session() as session:
            return list(
                session.scalars(
                    select(RemovedRemoteRepositoryRecord).order_by(
                        RemovedRemoteRepositoryRecord.removed_at.desc()
                    )
                )
            )

    def get(self, removal_id: str) -> RemovedRemoteRepositoryRecord | None:
        with self.database.session() as session:
            return session.get(RemovedRemoteRepositoryRecord, removal_id)

    def identities(self) -> set[tuple[str, str]]:
        """被移除的 ``(provider, remote_id)`` 集合，给列表同步做过滤。"""
        with self.database.session() as session:
            return {
                (provider, remote_id)
                for provider, remote_id in session.execute(
                    select(
                        RemovedRemoteRepositoryRecord.provider,
                        RemovedRemoteRepositoryRecord.remote_id,
                    )
                )
            }

    def discard(self, removal_id: str) -> bool:
        """恢复：删掉这条移除记录。返回是否真的删掉了一行。"""
        with self.database.session() as session:
            record = session.get(RemovedRemoteRepositoryRecord, removal_id)
            if record is None:
                return False
            session.delete(record)
            return True


class RemovedRemoteDocumentStore:
    """「从 DocMind 移除的远端文档」这张表的读写。

    写入点只有一个：本地移除一篇带 ``remote_id`` 的文档时。读取点也只有一个：
    同步循环在导入前查这份集合。恢复时删行 —— 但恢复还必须顺手清掉
    ``RepositorySyncStateStore`` 里那份陈旧快照，见那边的 ``discard``。
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    def add_many(self, repository_id: str, documents: Sequence[tuple[str, str]]) -> None:
        """登记一批 ``(remote_id, title)``，同一个远端文档重复移除只留一条。

        批量移除走一个事务：几十篇文档逐条开事务既慢又会在中途失败时留下半截
        状态 —— 而"移除了一半"对用户来说是最难解释的结果。
        """
        if not documents:
            return
        with self.database.session() as session:
            for remote_id, title in documents:
                session.execute(
                    sqlite_insert(RemovedRemoteDocumentRecord)
                    .values(
                        id=str(uuid4()),
                        repository_id=repository_id,
                        remote_id=remote_id,
                        title=title,
                    )
                    .on_conflict_do_update(
                        index_elements=[
                            RemovedRemoteDocumentRecord.repository_id,
                            RemovedRemoteDocumentRecord.remote_id,
                        ],
                        set_={"title": title},
                    )
                )
            session.flush()

    def identities(self, repository_id: str) -> set[str]:
        """被移除的远端文档 id 集合，给同步循环做过滤。"""
        with self.database.session() as session:
            return set(
                session.scalars(
                    select(RemovedRemoteDocumentRecord.remote_id).where(
                        RemovedRemoteDocumentRecord.repository_id == repository_id
                    )
                )
            )

    def list_for_repository(
        self, repository_id: str
    ) -> list[RemovedRemoteDocumentRecord]:
        with self.database.session() as session:
            return list(
                session.scalars(
                    select(RemovedRemoteDocumentRecord)
                    .where(RemovedRemoteDocumentRecord.repository_id == repository_id)
                    .order_by(RemovedRemoteDocumentRecord.removed_at.desc())
                )
            )

    def get(self, removal_id: str) -> RemovedRemoteDocumentRecord | None:
        with self.database.session() as session:
            return session.get(RemovedRemoteDocumentRecord, removal_id)

    def discard(self, removal_id: str) -> RemovedRemoteDocumentRecord | None:
        """恢复：删掉这条移除记录，返回它（调用方要用 repository_id/remote_id）。"""
        with self.database.session() as session:
            record = session.get(RemovedRemoteDocumentRecord, removal_id)
            if record is None:
                return None
            session.delete(record)
            session.flush()
            return record


class WebSearchRunStore:
    def __init__(self, database: Database):
        self.database = database

    def create(
        self, *, request_id: str, session_id: str, user_message_id: str, query: str
    ) -> WebSearchRunRecord:
        with self.database.session() as s:
            existing = s.scalar(
                select(WebSearchRunRecord).where(WebSearchRunRecord.request_id == request_id)
            )
            if existing is not None:
                return existing
            row = WebSearchRunRecord(
                request_id=request_id,
                session_id=session_id,
                user_message_id=user_message_id,
                query=query,
                status="running",
            )
            s.add(row)
            try:
                s.flush()
            except IntegrityError:
                s.rollback()
                return s.scalar(
                    select(WebSearchRunRecord).where(WebSearchRunRecord.request_id == request_id)
                )
            return row

    def by_request_id(self, request_id: str) -> WebSearchRunRecord | None:
        with self.database.session() as s:
            return s.scalar(
                select(WebSearchRunRecord).where(WebSearchRunRecord.request_id == request_id)
            )

    def complete(
        self, run_id: str, results: list[Any], provider: str | None = None
    ) -> WebSearchRunRecord:
        with self.database.session() as s:
            run = s.get(WebSearchRunRecord, run_id)
            # 与 fail() 同款守卫：缺了会 AttributeError('NoneType' has no 'status')
            # 而不是可读的 404，且因 run_id 外键约束，下面的 s.add 也会连带 IntegrityError。
            if run is None:
                raise DomainError("SEARCH_RUN_NOT_FOUND", "搜索任务不存在", 404)
            if provider:
                run.provider = provider[:32]
            for r in results:
                s.add(
                    WebSearchResultRecord(
                        run_id=run_id,
                        rank=r.rank,
                        canonical_url=str(r.canonical_url),
                        title=r.title,
                        snippet=r.snippet,
                        content=r.content,
                    )
                )
            run.status = "completed"
            run.completed_at = utc_now()
            s.flush()
            return run

    def get(self, run_id: str) -> WebSearchRunRecord | None:
        with self.database.session() as s:
            return s.get(WebSearchRunRecord, run_id)

    def fail(self, run_id: str, *, error_code: str) -> WebSearchRunRecord:
        """Persist a terminal failure without retaining a stale completion timestamp."""
        with self.database.session() as s:
            run = s.get(WebSearchRunRecord, run_id)
            if run is None:
                raise DomainError("SEARCH_RUN_NOT_FOUND", "搜索任务不存在", 404)
            run.status = "failed"
            run.error_code = error_code
            run.completed_at = None
            s.flush()
            return run

    def results(self, run_id: str) -> list[WebSearchResultRecord]:
        with self.database.session() as s:
            return list(
                s.scalars(
                    select(WebSearchResultRecord)
                    .where(WebSearchResultRecord.run_id == run_id)
                    .order_by(WebSearchResultRecord.rank)
                )
            )


class DocumentStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_all(self) -> list[DocumentRecord]:
        with self.database.session() as session:
            return list(session.scalars(select(DocumentRecord)))

    def list_for_repository(self, repository_id: str) -> list[DocumentRecord]:
        with self.database.session() as session:
            statement = (
                select(DocumentRecord)
                .where(DocumentRecord.repository_id == repository_id)
                .order_by(DocumentRecord.created_at.desc())
            )
            return list(session.scalars(statement))

    def source_urls(self, *, limit: int = 500) -> list[str]:
        """最近 `limit` 篇文档的来源相关字段。

        三个字段都要：`source_url` 是本地的来源、`remote_url` 是远端地址、
        `source_identity` 形如 `web:https://…`（网页导入）。少一个字段，那一类导入的
        文档就挖不出任何站点 —— 而"文档里的网址"正是这条信号的全部内容。
        """
        with self.database.session() as session:
            rows = session.execute(
                select(
                    DocumentRecord.source_url,
                    DocumentRecord.remote_url,
                    DocumentRecord.source_identity,
                )
                .order_by(DocumentRecord.created_at.desc(), DocumentRecord.id)
                .limit(limit)
            ).all()
        return [value for row in rows for value in row if value]

    def indexed_count_for_repository(self, repository_id: str) -> int:
        with self.database.session() as session:
            statement = select(func.count(func.distinct(DocumentChunkRecord.document_id))).where(
                DocumentChunkRecord.repository_id == repository_id
            )
            return int(session.scalar(statement) or 0)

    def get(self, document_id: str) -> DocumentRecord | None:
        with self.database.session() as session:
            return session.get(DocumentRecord, document_id)

    def create(self, document: DocumentRecord) -> DocumentRecord:
        with self.database.session() as session:
            session.add(document)
            session.flush()
            return document

    def find_by_source(self, repository_id: str, source_url: str) -> DocumentRecord | None:
        with self.database.session() as session:
            statement = select(DocumentRecord).where(
                DocumentRecord.repository_id == repository_id,
                DocumentRecord.source_url == source_url,
            )
            return session.scalar(statement)

    def find_by_identity(self, repository_id: str, source_identity: str) -> DocumentRecord | None:
        with self.database.session() as session:
            statement = select(DocumentRecord).where(
                DocumentRecord.repository_id == repository_id,
                DocumentRecord.source_identity == source_identity,
            )
            return session.scalar(statement)

    def find_by_hash(self, repository_id: str, content_hash: str) -> DocumentRecord | None:
        with self.database.session() as session:
            statement = select(DocumentRecord).where(
                DocumentRecord.repository_id == repository_id,
                DocumentRecord.content_hash == content_hash,
            )
            return session.scalar(statement)

    def find_by_remote_id(self, repository_id: str, remote_id: str) -> DocumentRecord | None:
        with self.database.session() as session:
            statement = select(DocumentRecord).where(
                DocumentRecord.repository_id == repository_id,
                DocumentRecord.remote_id == remote_id,
            )
            return session.scalar(statement)

    def mark_remote_deleted(self, document_id: str) -> None:
        with self.database.session() as session:
            document = session.get(DocumentRecord, document_id)
            if document is not None:
                document.remote_deleted = True
                document.updated_at = utc_now()
                session.flush()

    def set_local_dirty(self, document_id: str, dirty: bool) -> None:
        with self.database.session() as session:
            document = session.get(DocumentRecord, document_id)
            if document is not None:
                document.local_dirty = dirty
                document.updated_at = utc_now()
                session.flush()

    def set_sync_state(self, document_id: str, state: str) -> None:
        with self.database.session() as session:
            document = session.get(DocumentRecord, document_id)
            if document is not None:
                document.sync_state = state
                document.updated_at = utc_now()
                session.flush()

    def mark_local(self, document_id: str) -> None:
        with self.database.session() as session:
            document = session.get(DocumentRecord, document_id)
            if document is not None:
                document.status = "local"
                document.sync_state = "local"
                document.local_dirty = True
                document.updated_at = utc_now()
                session.flush()

    def update_synced_content(
        self,
        document_id: str,
        *,
        title: str,
        content_hash: str,
        source_url: str,
        markdown_path: str | None = None,
        remote_path: Sequence[str] | None = None,
    ) -> None:
        with self.database.session() as session:
            document = session.get(DocumentRecord, document_id)
            if document is None:
                raise DomainError("IMPORT_STATE_CONFLICT", "导入文档不存在", 409)
            document.title = title
            document.content_hash = content_hash
            document.source_url = source_url
            if markdown_path is not None:
                document.markdown_path = markdown_path
            # None 表示"这次没带路径信息"，不要用它把已知路径清成根目录 ——
            # 只有真正读到路径（可能是空列表 = 确实在根目录）才写。
            if remote_path is not None:
                document.remote_path_json = json.dumps(list(remote_path))
            document.remote_deleted = False
            document.updated_at = utc_now()
            session.flush()

    def update_import_metadata(
        self,
        document_id: str,
        *,
        title: str,
        source_url: str,
        raw_path: str | None,
        markdown_path: str,
        content_hash: str,
        source_identity: str | None = None,
        source_revision: str | None = None,
        source_path: str | None = None,
        original_revision: str | None = None,
    ) -> DocumentRecord:
        with self.database.session() as session:
            document = session.get(DocumentRecord, document_id)
            if document is None:
                raise DomainError("IMPORT_STATE_CONFLICT", "导入文档不存在", 409)
            document.title = title
            document.source_url = source_url
            document.raw_path = raw_path
            document.source_path = source_path
            document.original_revision = original_revision
            document.markdown_path = markdown_path
            document.content_hash = content_hash
            if source_identity is not None:
                document.source_identity = source_identity
            if source_revision is not None:
                document.source_revision = source_revision
            document.source_type = "import"
            document.updated_at = utc_now()
            session.flush()
            return document

    def update_linked_original(
        self,
        document_id: str,
        *,
        source_path: str,
        original_revision: str | None = None,
    ) -> DocumentRecord:
        """重新绑定原件路径（重新定位），或在重新转换后记下这一版。

        `original_revision=None` 表示"只改绑定、不动 revision"—— 重新定位就是这样：
        状态随后由"新文件的 mtime:size 是否等于当初记录的那一个"算出来，于是
        改名/移动（内容没变）自动回到 available，换成另一个文件则显示 changed。
        """
        with self.database.session() as session:
            document = session.get(DocumentRecord, document_id)
            if document is None:
                raise DomainError("IMPORT_STATE_CONFLICT", "导入文档不存在", 409)
            document.source_path = source_path
            if original_revision is not None:
                document.original_revision = original_revision
            document.updated_at = utc_now()
            session.flush()
            return document

    def update_remote(
        self, document_id: str, *, remote_id: str, remote_url: str | None
    ) -> DocumentRecord:
        with self.database.session() as session:
            document = session.get(DocumentRecord, document_id)
            if document is None:
                raise DomainError("IMPORT_STATE_CONFLICT", "导入文档不存在", 409)
            document.remote_id = remote_id
            document.remote_url = remote_url
            document.status = "uploaded"
            document.updated_at = utc_now()
            session.flush()
            return document

    def update_editor(
        self,
        document_id: str,
        *,
        title: str,
        remote_id: str | None,
        remote_url: str | None,
        markdown_path: str,
        source_url: str | None,
    ) -> DocumentRecord:
        with self.database.session() as session:
            document = session.get(DocumentRecord, document_id)
            if document is None:
                raise DomainError("NOT_FOUND", "资源不存在", 404)
            document.title = title
            document.remote_id = remote_id
            document.remote_url = remote_url
            document.markdown_path = markdown_path
            document.source_url = source_url
            document.updated_at = utc_now()
            session.flush()
            return document

    def save_with_chunks(self, document: DocumentRecord, chunks: list[DocumentChunkRecord]) -> None:
        with self.database.session() as session:
            saved_document = session.merge(document)
            saved_document.chunk_count = len(chunks)
            saved_document.updated_at = utc_now()
            session.flush()
            for chunk in chunks:
                chunk.document_id = saved_document.id
                chunk.repository_id = saved_document.repository_id
                session.merge(chunk)

    def replace_chunks(self, document_id: str, chunks: list[DocumentChunkRecord]) -> None:
        with self.database.session() as session:
            document = session.get(DocumentRecord, document_id)
            if document is None:
                return
            session.execute(
                delete(DocumentChunkRecord).where(DocumentChunkRecord.document_id == document_id)
            )
            for chunk in chunks:
                chunk.document_id = document_id
                chunk.repository_id = document.repository_id
                session.add(chunk)
            document.chunk_count = len(chunks)
            document.updated_at = utc_now()

    def vector_ids(self, document_id: str) -> list[str]:
        with self.database.session() as session:
            statement = (
                select(DocumentChunkRecord)
                .where(DocumentChunkRecord.document_id == document_id)
                .order_by(DocumentChunkRecord.chunk_index)
            )
            chunks = list(session.scalars(statement))
            return [chunk.vector_id or chunk.id for chunk in chunks]

    def owned_vector_ids(self, vector_ids: list[str]) -> set[str]:
        """Return vector IDs still referenced by any active chunk."""
        if not vector_ids:
            return set()
        with self.database.session() as session:
            statement = select(DocumentChunkRecord.id, DocumentChunkRecord.vector_id).where(
                or_(
                    DocumentChunkRecord.vector_id.in_(vector_ids),
                    and_(
                        DocumentChunkRecord.vector_id.is_(None),
                        DocumentChunkRecord.id.in_(vector_ids),
                    ),
                )
            )
            return {vector_id or chunk_id for chunk_id, vector_id in session.execute(statement)}

    def list_chunks(self, document_id: str) -> list[DocumentChunkRecord]:
        with self.database.session() as session:
            statement = (
                select(DocumentChunkRecord)
                .where(DocumentChunkRecord.document_id == document_id)
                .order_by(DocumentChunkRecord.chunk_index)
            )
            return list(session.scalars(statement))

    def chunk_image_payloads(self, document_id: str) -> list[str]:
        """这篇文档所有 chunk 的 ``images_json``，按 chunk 顺序。

        只取这一列，不取整个 chunk 行：文档详情要的是插图清单，而 ``text`` 列在几百个
        chunk 上是几百 KB，取回来只为丢掉。

        走仓储而不是在 API 层访问 ``document.chunks``：那是懒加载关系，session 一关就
        detach，访问即抛 ``DetachedInstanceError``。仓储的 session 边界是明确的，这个坑
        不该让每个调用点各自记着。
        """
        with self.database.session() as session:
            statement = (
                select(DocumentChunkRecord.images_json)
                .where(DocumentChunkRecord.document_id == document_id)
                .order_by(DocumentChunkRecord.chunk_index)
            )
            return [value or "" for value in session.scalars(statement)]

    def delete_local(self, document_id: str) -> None:
        with self.database.session() as session:
            document = session.get(DocumentRecord, document_id)
            if document is not None:
                session.delete(document)

    def restore_snapshot(
        self,
        document: dict[str, Any],
        chunks: list[DocumentChunkRecord],
    ) -> DocumentRecord:
        """Restore document metadata and chunks in one SQLite transaction."""
        with self.database.session() as session:
            record = session.get(DocumentRecord, document["id"])
            if record is None:
                record = DocumentRecord(
                    id=document["id"],
                    repository_id=document["repository_id"],
                    title=document["title"],
                )
                session.add(record)
            for field in (
                "repository_id",
                "remote_id",
                "title",
                "source_url",
                "raw_path",
                "markdown_path",
                "source_type",
                "content_hash",
                "chunk_count",
                "status",
                "remote_url",
                "source_identity",
                "source_revision",
            ):
                if field in document:
                    setattr(record, field, document[field])
            for field in ("created_at", "updated_at"):
                value = document.get(field)
                if isinstance(value, str):
                    value = datetime.fromisoformat(value)
                if value is not None:
                    setattr(record, field, value)
            session.execute(
                delete(DocumentChunkRecord).where(DocumentChunkRecord.document_id == record.id)
            )
            for chunk in chunks:
                session.add(chunk)
            session.flush()
            return record


class ImportJobStore:
    _INTERRUPTED_STATES = (ImportStatus.PARSING, ImportStatus.UPLOADING, ImportStatus.INDEXING)
    _ACTIVE_STATES = (ImportStatus.PENDING, *_INTERRUPTED_STATES)

    def __init__(self, database: Database) -> None:
        self.database = database

    def create(self, job: ImportJobRecord) -> ImportJobRecord:
        with self.database.session() as session:
            bound = _batch_confirmation_session.get()
            if bound is not None and bound is not session:
                raise DomainError(
                    "BATCH_STATE_CONFLICT",
                    "批次子任务必须在确认事务中保留",
                    409,
                )
            session.add(job)
            session.flush()
            return job

    def create_cleanup(
        self, *, repository_id: str, document_id: str, vector_ids: list[str]
    ) -> ImportJobRecord:
        payload = json.dumps(
            {"repository_id": repository_id, "document_id": document_id, "vector_ids": vector_ids}
        )
        return self.create(
            ImportJobRecord(
                id=str(uuid4()),
                source_kind="api_cleanup",
                source_value=payload,
                repository_id=repository_id,
                document_id=document_id,
            )
        )

    def list_cleanups(self) -> list[ImportJobRecord]:
        with self.database.session() as session:
            return list(
                session.scalars(
                    select(ImportJobRecord).where(ImportJobRecord.source_kind == "api_cleanup")
                )
            )

    def delete_job(self, job_id: str) -> None:
        with self.database.session() as session:
            record = session.get(ImportJobRecord, job_id)
            if record is not None:
                session.delete(record)

    def reserve(self, job: ImportJobRecord, *, fingerprint: str) -> ImportJobRecord:
        with self.database.session() as session:
            bound = _batch_confirmation_session.get()
            if bound is not None and bound is not session:
                raise DomainError(
                    "BATCH_STATE_CONFLICT",
                    "批次子任务必须在确认事务中保留",
                    409,
                )
            return self.reserve_in_session(session, job, fingerprint=fingerprint)

    def reserve_in_session(
        self,
        session: Session,
        job: ImportJobRecord,
        *,
        fingerprint: str,
    ) -> ImportJobRecord:
        statement = select(ImportJobRecord).where(
            ImportJobRecord.repository_id == job.repository_id,
            ImportJobRecord.state.in_(self._ACTIVE_STATES),
        )
        for active in session.scalars(statement):
            if _source_fingerprint(active.source_value) == fingerprint:
                raise DomainError("IMPORT_ALREADY_RUNNING", "导入任务正在运行", 409)
        session.add(job)
        session.flush()
        session.info.setdefault("batch_reserved_job_ids", set()).add(job.id)
        return job

    def get(self, job_id: str) -> ImportJobRecord | None:
        with self.database.session() as session:
            return session.get(ImportJobRecord, job_id)

    def list(self) -> list[ImportJobRecord]:
        with self.database.session() as session:
            return list(
                session.scalars(select(ImportJobRecord).order_by(ImportJobRecord.created_at))
            )

    def has_active_for_repository(self, repository_id: str) -> bool:
        """这个知识库上是否还有导入正在跑。

        删除知识库前必须先问这一句：导入流水线正在往这个仓库写 documents 与
        chunks，而两列都有指向 repositories 的外键。删掉之后它下一次写入撞的是
        外键约束，用户看到的是"导入失败"加一个数据库错误，而不是"你正在导入"。
        检查与删除之间仍有理论上的竞争窗口；单进程桌面应用里这个窗口不值得再加锁。
        """
        with self.database.session() as session:
            active = session.scalar(
                select(func.count())
                .select_from(ImportJobRecord)
                .where(
                    ImportJobRecord.repository_id == repository_id,
                    ImportJobRecord.state.in_(self._ACTIVE_STATES),
                )
            )
        return bool(active)

    def transition(
        self,
        job_id: str,
        *,
        expected: set[ImportStatus],
        target: ImportStatus,
        progress: int,
        message: str,
    ) -> ImportJobRecord:
        with self.database.session() as session:
            job = session.get(ImportJobRecord, job_id)
            if job is None or job.state not in expected:
                raise DomainError("IMPORT_STATE_CONFLICT", "导入任务状态冲突", 409)
            ensure_transition_allowed(job.state, target)
            now = utc_now()
            job.state = target
            job.current_stage = target.value
            job.progress = progress
            job.message = message
            job.updated_at = now
            if target in self._INTERRUPTED_STATES and job.started_at is None:
                job.started_at = now
            if target in (ImportStatus.COMPLETED, ImportStatus.FAILED, ImportStatus.CANCELLED):
                job.completed_at = now
            session.flush()
            return job

    def update_progress(
        self, job_id: str, *, expected: ImportStatus, progress: int, message: str
    ) -> ImportJobRecord:
        with self.database.session() as session:
            job = session.get(ImportJobRecord, job_id)
            if job is None or job.state != expected:
                raise DomainError("IMPORT_STATE_CONFLICT", "导入任务状态冲突", 409)
            job.progress = progress
            job.message = message
            job.updated_at = utc_now()
            session.flush()
            return job

    def attach_document(self, job_id: str, document_id: str) -> ImportJobRecord:
        with self.database.session() as session:
            job = session.get(ImportJobRecord, job_id)
            if job is None:
                raise DomainError("IMPORT_STATE_CONFLICT", "导入任务状态冲突", 409)
            job.document_id = document_id
            job.updated_at = utc_now()
            session.flush()
            return job

    def merge_source_metadata(self, job_id: str, updates: dict[str, Any]) -> ImportJobRecord:
        with self.database.session() as session:
            job = session.get(ImportJobRecord, job_id)
            if job is None:
                raise DomainError("IMPORT_STATE_CONFLICT", "导入任务状态冲突", 409)
            metadata = _normalized_source_metadata(job)
            requested_sequence = updates.get("last_event_sequence")
            if isinstance(requested_sequence, int) and not isinstance(requested_sequence, bool):
                updates = {
                    **updates,
                    "last_event_sequence": max(metadata["last_event_sequence"], requested_sequence),
                }
            metadata.update(updates)
            metadata["version"] = 2
            job.source_value = _dump_source_metadata(metadata)
            job.updated_at = utc_now()
            session.flush()
            return job

    def allocate_event_sequence(self, job_id: str) -> int:
        with self.database.session() as session:
            job = session.get(ImportJobRecord, job_id)
            if job is None:
                raise DomainError("IMPORT_STATE_CONFLICT", "导入任务状态冲突", 409)
            metadata = _normalized_source_metadata(job)
            current = metadata["last_event_sequence"]
            sequence = current + 1
            metadata["last_event_sequence"] = sequence
            job.source_value = _dump_source_metadata(metadata)
            job.updated_at = utc_now()
            session.flush()
            return sequence

    def update_stale_vector_ids(self, job_id: str, vector_ids: list[str]) -> None:
        with self.database.session() as session:
            job = session.get(ImportJobRecord, job_id)
            if job is None:
                raise DomainError("IMPORT_STATE_CONFLICT", "导入任务状态冲突", 409)
            metadata = _normalized_source_metadata(job)
            metadata["stale_vector_ids"] = list(dict.fromkeys(vector_ids))
            job.source_value = _dump_source_metadata(metadata)
            job.updated_at = utc_now()
            session.flush()

    def fail(self, job_id: str, *, code: str, message: str, retryable: bool) -> ImportJobRecord:
        with self.database.session() as session:
            job = session.get(ImportJobRecord, job_id)
            if job is None or job.state not in self._INTERRUPTED_STATES:
                raise DomainError("IMPORT_STATE_CONFLICT", "导入任务状态冲突", 409)
            ensure_transition_allowed(job.state, ImportStatus.FAILED)
            now = utc_now()
            job.state = ImportStatus.FAILED
            job.message = message
            job.error_code = code
            job.error_message = message
            job.retryable = retryable
            job.completed_at = now
            job.updated_at = now
            session.flush()
            return job

    def request_cancel(self, job_id: str) -> ImportJobRecord:
        with self.database.session() as session:
            job = session.get(ImportJobRecord, job_id)
            if job is None or job.state in (
                ImportStatus.COMPLETED,
                ImportStatus.FAILED,
                ImportStatus.CANCELLED,
            ):
                raise DomainError("IMPORT_STATE_CONFLICT", "导入任务状态冲突", 409)
            job.cancel_requested = True
            job.updated_at = utc_now()
            session.flush()
            return job

    def cancel(self, job_id: str) -> ImportJobRecord:
        job = self.get(job_id)
        if job is None:
            raise DomainError("IMPORT_STATE_CONFLICT", "导入任务状态冲突", 409)
        return self.transition(
            job_id,
            expected={job.state},
            target=ImportStatus.CANCELLED,
            progress=job.progress,
            message="导入已取消",
        )

    def reset_for_retry(self, job_id: str) -> ImportJobRecord:
        with self.database.session() as session:
            job = session.get(ImportJobRecord, job_id)
            if job is None or job.state != ImportStatus.FAILED or not job.retryable:
                raise DomainError("IMPORT_STATE_CONFLICT", "导入任务不可重试", 409)
            try:
                resume_stage = ImportStatus(job.current_stage or ImportStatus.PARSING.value)
            except ValueError:
                resume_stage = ImportStatus.PARSING
            if resume_stage not in self._INTERRUPTED_STATES:
                resume_stage = ImportStatus.PARSING
            job.state = (
                ImportStatus.PENDING if resume_stage == ImportStatus.PARSING else resume_stage
            )
            job.error_code = None
            job.error_message = None
            job.retryable = False
            job.cancel_requested = False
            job.completed_at = None
            job.updated_at = utc_now()
            session.flush()
            return job

    def recover_interrupted(self) -> int:
        with self.database.session() as session:
            statement = select(ImportJobRecord).where(
                ImportJobRecord.state.in_(self._INTERRUPTED_STATES)
            )
            jobs = list(session.scalars(statement))
            now = utc_now()
            for job in jobs:
                job.current_stage = job.current_stage or job.state.value
                metadata = _normalized_source_metadata(job)
                if job.cancel_requested and metadata.get("coherence_pending") is not True:
                    job.state = ImportStatus.CANCELLED
                    job.message = "导入已取消"
                    job.error_code = None
                    job.error_message = None
                    job.retryable = False
                else:
                    job.state = ImportStatus.FAILED
                    job.message = "应用重启导致任务中断"
                    job.error_code = "APP_RESTARTED"
                    job.error_message = "应用重启导致任务中断"
                    job.retryable = True
                job.completed_at = now
                job.updated_at = now
            return len(jobs)


class BatchImportStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_batch(self, batch: BatchImportRecord) -> BatchImportRecord:
        batch.source_descriptor_json = _source_descriptor_json(batch.source_descriptor_json)
        with self.database.session() as session:
            session.add(batch)
            session.flush()
            return batch

    def get(self, batch_id: str) -> BatchImportRecord | None:
        with self.database.session() as session:
            return session.get(BatchImportRecord, batch_id)

    def list_batches(self) -> list[BatchImportRecord]:
        with self.database.session() as session:
            return list(
                session.scalars(
                    select(BatchImportRecord).order_by(BatchImportRecord.created_at.desc())
                )
            )

    def get_item(self, item_id: str) -> BatchItemRecord | None:
        with self.database.session() as session:
            return session.get(BatchItemRecord, item_id)

    def list_item_records(self, batch_id: str) -> list[BatchItemRecord]:
        with self.database.session() as session:
            return list(
                session.scalars(
                    select(BatchItemRecord)
                    .where(BatchItemRecord.batch_id == batch_id)
                    .order_by(BatchItemRecord.ordinal, BatchItemRecord.id)
                )
            )

    def list_items(
        self,
        batch_id: str,
        cursor: str | None = None,
        state: BatchItemState | str | None = None,
        selected: bool | None = None,
        *,
        limit: int = 100,
    ) -> BatchItemPage:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        after = _decode_batch_item_cursor(cursor) if cursor is not None else None
        with self.database.session() as session:
            conditions = [BatchItemRecord.batch_id == batch_id]
            if state is not None:
                conditions.append(BatchItemRecord.state == BatchItemState(state))
            if selected is not None:
                conditions.append(BatchItemRecord.selected == selected)
            if after is not None:
                ordinal, item_id = after
                conditions.append(
                    or_(
                        BatchItemRecord.ordinal > ordinal,
                        and_(BatchItemRecord.ordinal == ordinal, BatchItemRecord.id > item_id),
                    )
                )
            statement = (
                select(BatchItemRecord)
                .where(*conditions)
                .order_by(BatchItemRecord.ordinal, BatchItemRecord.id)
                .limit(limit + 1)
            )
            records = list(session.scalars(statement))
            has_more = len(records) > limit
            page_records = records[:limit]
            next_cursor = _encode_batch_item_cursor(page_records[-1]) if has_more else None
            return BatchItemPage(
                items=[_batch_item_view(record) for record in page_records], next_cursor=next_cursor
            )

    def insert_discovered_items(
        self, batch_id: str, items: list[BatchItemRecord]
    ) -> list[BatchItemRecord]:
        with self.database.session() as session:
            batch = session.get(BatchImportRecord, batch_id)
            if batch is None:
                raise DomainError("BATCH_NOT_FOUND", "批次不存在", 404)
            max_ordinal = session.scalar(
                select(func.max(BatchItemRecord.ordinal)).where(
                    BatchItemRecord.batch_id == batch_id
                )
            )
            next_ordinal = (int(max_ordinal) if max_ordinal is not None else -1) + 1
            for offset, item in enumerate(items):
                item.batch_id = batch_id
                item.ordinal = next_ordinal + offset
                item.cached_source_json = _cached_source_json(item.cached_source_json)
                target = self._document_target(session, batch.repository_id, item)
                item.existing_document_id = target.id if target is not None else None
                supplied_actions = _decode_allowed_actions(item.allowed_actions_json)
                # Legacy callers may provide an attach_remote snapshot before
                # the binding is persisted; retain it so confirmation returns
                # the stable binding validation error.
                if "attach_remote" in supplied_actions and not _has_remote_binding(
                    item.remote_binding_json
                ):
                    actions = supplied_actions
                else:
                    actions = (
                        ["update", "skip"]
                        if target is not None
                        else (
                            ["attach_remote", "skip"]
                            if _has_remote_binding(item.remote_binding_json)
                            else ["create", "skip"]
                        )
                    )
                item.allowed_actions_json = _allowed_actions_json(actions)
                session.add(item)
            batch.total_count += len(items)
            batch.updated_at = utc_now()
            session.flush()
            return items

    @staticmethod
    def _document_target(
        session: Session, repository_id: str | None, item: BatchItemRecord
    ) -> DocumentRecord | None:
        """按来源标识 → 内容哈希回退，找出这条 item 对应的既有文档。

        两条调用路径（insert_discovered_items 与 _confirm_and_reserve_in_session）
        曾各有一份逐字相同的实现，只有 `repository_id` 可空的处理不同 ——
        现在统一走这里，可空判断留在本方法内。
        """
        if not repository_id:
            return None
        target = session.scalar(
            select(DocumentRecord).where(
                DocumentRecord.repository_id == repository_id,
                DocumentRecord.source_identity == item.source_identity,
            )
        )
        if target is not None:
            return target
        revision = item.source_revision.removeprefix("sha256:")
        if len(revision) != 64:
            return None
        return session.scalar(
            select(DocumentRecord).where(
                DocumentRecord.repository_id == repository_id,
                DocumentRecord.content_hash == revision,
            )
        )

    def fail_discovery(
        self, batch_id: str, *, code: str, message: str, retryable: bool
    ) -> BatchImportRecord:
        with self.database.session() as session:
            batch = session.get(BatchImportRecord, batch_id)
            if batch is None:
                raise DomainError("BATCH_NOT_FOUND", "批次不存在", 404)
            now = utc_now()
            if batch.state == BatchState.DISCOVERING:
                transition(batch.state, BatchState.FAILED)
                batch.state = BatchState.FAILED
            batch.error_code = code
            batch.error_message = message
            batch.retryable = retryable
            batch.message = message
            batch.completed_at = now
            batch.updated_at = now
            session.flush()
            return batch

    def set_confirmation(self, batch_id: str, confirmation: ConfirmBatchInput) -> BatchImportRecord:
        decisions = {
            str(item.item_id): BatchItemDecision(item.decision) for item in confirmation.items
        }
        if len(decisions) != len(confirmation.items):
            raise DomainError("BATCH_CONFIRMATION_INVALID", "确认项重复", 422)
        with self.database.session() as session:
            batch = session.get(BatchImportRecord, batch_id)
            if batch is None:
                raise DomainError("BATCH_NOT_FOUND", "批次不存在", 404)
            if batch.discovery_version != confirmation.discovery_version:
                raise _stale_confirmation("发现结果已更新，请重新确认")
            if batch.state != BatchState.AWAITING_CONFIRMATION:
                raise DomainError("BATCH_STATE_CONFLICT", "批次当前不可确认", 409)
            items = list(
                session.scalars(
                    select(BatchItemRecord)
                    .where(BatchItemRecord.batch_id == batch_id)
                    .order_by(BatchItemRecord.ordinal)
                )
            )
            item_ids = {item.id for item in items}
            if not decisions.keys() <= item_ids:
                raise DomainError("BATCH_CONFIRMATION_INVALID", "确认项不属于该批次", 422)
            for item in items:
                ensure_item_mutable(item.state)
                decision = decisions.get(item.id)
                allowed_actions = _decode_allowed_actions(item.allowed_actions_json)
                if decision is not None and decision.value not in allowed_actions:
                    raise DomainError("BATCH_CONFIRMATION_INVALID", "批次项不允许该确认操作", 422)
                if decision == BatchItemDecision.ATTACH_REMOTE and not _has_remote_binding(
                    item.remote_binding_json
                ):
                    raise DomainError(
                        "BATCH_CONFIRMATION_INVALID", "attach_remote 需要远端绑定", 422
                    )
                # Preserve a previously reserved child when a confirmation
                # request is retried after a partial transaction.
                if item.import_job_id is None:
                    item.decision = decision
                    item.selected = decision not in {None, BatchItemDecision.SKIP}
                    item.state = (
                        BatchItemState.SKIPPED
                        if decision == BatchItemDecision.SKIP
                        else BatchItemState.DISCOVERED
                    )
                elif decision != item.decision:
                    raise DomainError(
                        "BATCH_CONFIRMATION_INVALID", "已保留的批次项决策不可修改", 422
                    )
                item.updated_at = utc_now()
            batch.selected_count = sum(item.selected for item in items)
            batch.skipped_count = sum(item.state == BatchItemState.SKIPPED for item in items)
            batch.updated_at = utc_now()
            session.flush()
            return batch

    def confirm_and_reserve(
        self,
        batch_id: str,
        confirmation: ConfirmBatchInput,
        *,
        reserve_child: Callable[..., str],
        reserve_children: Callable[..., dict[str, str]] | None = None,
        validate_sources: Callable[..., Any] | None = None,
    ) -> BatchImportRecord:
        """Confirm a discovery snapshot and bind all children in one write transaction."""
        decisions = {
            str(item.item_id): BatchItemDecision(item.decision) for item in confirmation.items
        }
        if len(decisions) != len(confirmation.items):
            raise DomainError("BATCH_CONFIRMATION_INVALID", "确认项重复", 422)
        with self.database.session() as session, self.database.bind_confirmation_session(session):
            token = _batch_confirmation_session.set(session)
            try:
                return self._confirm_and_reserve_in_session(
                    session,
                    batch_id,
                    confirmation,
                    reserve_child=reserve_child,
                    reserve_children=reserve_children,
                    validate_sources=validate_sources,
                )
            finally:
                _batch_confirmation_session.reset(token)

    def _confirm_and_reserve_in_session(
        self,
        session: Session,
        batch_id: str,
        confirmation: ConfirmBatchInput,
        *,
        reserve_child: Callable[..., str],
        reserve_children: Callable[..., dict[str, str]] | None,
        validate_sources: Callable[..., Any] | None,
    ) -> BatchImportRecord:
        if self.database.url.startswith("sqlite"):
            session.execute(text("BEGIN IMMEDIATE"))
        decisions = {
            str(item.item_id): BatchItemDecision(item.decision) for item in confirmation.items
        }
        batch = session.get(BatchImportRecord, batch_id)
        if batch is None:
            raise DomainError("BATCH_NOT_FOUND", "批次不存在", 404)
        if batch.state != BatchState.AWAITING_CONFIRMATION:
            raise DomainError("BATCH_STATE_CONFLICT", "批次当前不可确认", 409)
        if batch.discovery_version != confirmation.discovery_version:
            raise _stale_confirmation("发现结果已更新，请重新确认")
        repository = (
            session.get(RepositoryRecord, batch.repository_id)
            if batch.repository_id is not None
            else None
        )
        if repository is None or not repository.remote_id:
            raise _stale_confirmation("目标知识库已变化，请重新确认")
        items = list(
            session.scalars(
                select(BatchItemRecord)
                .where(BatchItemRecord.batch_id == batch_id)
                .order_by(BatchItemRecord.ordinal, BatchItemRecord.id)
            )
        )
        item_ids = {item.id for item in items}
        if not decisions.keys() <= item_ids:
            raise DomainError("BATCH_CONFIRMATION_INVALID", "确认项不属于该批次", 422)

        targets: dict[str, DocumentRecord | None] = {}
        selected_items: list[BatchItemRecord] = []
        for item in items:
            ensure_item_mutable(item.state)
            if item.state != BatchItemState.DISCOVERED or item.import_job_id is not None:
                raise DomainError("BATCH_STATE_CONFLICT", "批次项当前不可确认", 409)
            decision = decisions.get(item.id)
            if decision is None:
                continue
            target = self._current_document_target(
                session,
                batch.repository_id or "",
                item,
            )
            current_actions = self._current_actions(item, target)
            persisted_actions = set(_decode_allowed_actions(item.allowed_actions_json))
            if (
                persisted_actions != current_actions
                or item.existing_document_id != (target.id if target is not None else None)
                or decision.value not in current_actions
                or (
                    decision == BatchItemDecision.ATTACH_REMOTE
                    and not _has_remote_binding(item.remote_binding_json)
                )
            ):
                raise _stale_confirmation()
            targets[item.id] = target
            if decision != BatchItemDecision.SKIP:
                selected_items.append(item)

        if validate_sources is not None and selected_items:
            try:
                validation = validate_sources(
                    selected_items,
                    batch=batch,
                    repository_id=batch.repository_id,
                    session=session,
                )
            except DomainError as error:
                if error.code in {"BATCH_SOURCE_CHANGED", "BATCH_STALE_CONFIRMATION"}:
                    raise _stale_confirmation(error.message) from None
                raise
            if inspect.isawaitable(validation):
                raise TypeError(
                    "batch source validation must be synchronous inside the transaction"
                )

        for item in items:
            decision = decisions.get(item.id)
            item.decision = decision
            item.selected = decision not in {None, BatchItemDecision.SKIP}
            if decision is None or decision == BatchItemDecision.SKIP:
                item.decision = BatchItemDecision.SKIP
                item.state = BatchItemState.SKIPPED
            if decision is not None:
                target = targets[item.id]
                item.existing_document_id = target.id if target is not None else None

        job_ids: Any = None
        reserved_marker = session.info.setdefault("batch_reserved_job_ids", set())
        existing_job_ids = set(session.scalars(select(ImportJobRecord.id)))
        existing_job_ids.update(
            obj.id
            for obj in session.new
            if isinstance(obj, ImportJobRecord) and isinstance(obj.id, str)
        )
        created_job_ids: set[str] = set()

        def track_new_jobs(current_session: Session, *_: Any) -> None:
            created_job_ids.update(
                obj.id
                for obj in current_session.new
                if isinstance(obj, ImportJobRecord) and obj.id not in existing_job_ids
            )

        event.listen(session, "after_flush", track_new_jobs)
        try:
            if reserve_children is not None and selected_items:
                job_ids = reserve_children(
                    selected_items,
                    batch=batch,
                    repository_id=batch.repository_id,
                    session=session,
                )
            else:
                job_ids = {
                    item.id: reserve_child(
                        item,
                        batch=batch,
                        repository_id=batch.repository_id,
                        session=session,
                    )
                    for item in selected_items
                }
            if inspect.isawaitable(job_ids):
                raise TypeError("batch reservation must be synchronous inside the transaction")
            if not isinstance(job_ids, dict) or set(job_ids) != {
                item.id for item in selected_items
            }:
                raise RuntimeError("batch reservation returned invalid child mapping")
            # Flush once more so callbacks that only add a child (without
            # calling ``flush`` themselves) are tracked by the listener.
            session.flush()
            allowed_job_ids = reserved_marker | created_job_ids
            returned_job_ids = [value for value in job_ids.values() if isinstance(value, str)]
            if len(returned_job_ids) != len(set(returned_job_ids)):
                raise RuntimeError("batch reservation returned duplicate child jobs")
            if created_job_ids - set(returned_job_ids):
                raise RuntimeError("batch reservation created unmapped child jobs")
            for item in selected_items:
                job_id = job_ids[item.id]
                if not isinstance(job_id, str) or (job_id not in allowed_job_ids):
                    raise RuntimeError("batch reservation must use the confirmation session")
                if job_id in existing_job_ids:
                    raise RuntimeError("batch reservation returned an existing child job")
                job = session.get(ImportJobRecord, job_id)
                if job is None or job.repository_id != batch.repository_id:
                    raise RuntimeError("batch reservation returned invalid child job")
                expected_fingerprint = item.source_revision.removeprefix("sha256:")
                actual_fingerprint = _source_fingerprint(job.source_value)
                if len(expected_fingerprint) == 64 and actual_fingerprint != expected_fingerprint:
                    raise RuntimeError("batch reservation returned mismatched child fingerprint")
        except DomainError as error:
            if created_job_ids:
                session.execute(
                    delete(ImportJobRecord).where(ImportJobRecord.id.in_(created_job_ids))
                )
            if error.code in {"BATCH_SOURCE_CHANGED", "BATCH_STALE_CONFIRMATION"}:
                raise _stale_confirmation(error.message) from None
            raise
        except (TypeError, RuntimeError):
            if created_job_ids:
                session.execute(
                    delete(ImportJobRecord).where(ImportJobRecord.id.in_(created_job_ids))
                )
            raise
        finally:
            event.remove(session, "after_flush", track_new_jobs)

        now = utc_now()
        for item in items:
            decision = decisions.get(item.id)
            item.decision = decision
            item.selected = decision not in {None, BatchItemDecision.SKIP}
            item.existing_document_id = (
                targets[item.id].id
                if decision is not None and targets[item.id] is not None
                else None
            )
            if decision == BatchItemDecision.SKIP:
                item.state = BatchItemState.SKIPPED
            elif decision is not None:
                job_id = job_ids.get(item.id)
                if not isinstance(job_id, str):
                    raise RuntimeError("batch reservation did not return a child job id")
                session.flush()
                if session.get(ImportJobRecord, job_id) is None:
                    raise RuntimeError("batch reservation did not persist its child job")
                item.import_job_id = job_id
                item.state = BatchItemState.QUEUED
            item.updated_at = now

        batch.selected_count = sum(item.selected for item in items)
        batch.skipped_count = sum(item.state == BatchItemState.SKIPPED for item in items)
        target_state = BatchState.RUNNING if batch.selected_count else BatchState.COMPLETED
        transition(batch.state, target_state)
        batch.state = target_state
        batch.message = "批次导入中" if target_state == BatchState.RUNNING else "批次无待导入项"
        if target_state == BatchState.RUNNING:
            batch.started_at = now
        else:
            batch.completed_at = now
            batch.progress = 100
        batch.updated_at = now
        session.flush()
        return batch

    @staticmethod
    def _current_document_target(
        session: Session,
        repository_id: str,
        item: BatchItemRecord,
    ) -> DocumentRecord | None:
        # 与 _document_target 是同一段逻辑（来源标识 → 内容哈希回退），
        # 此调用点保证 repository_id 非空，直接委托避免两份实现漂移。
        return BatchImportStore._document_target(session, repository_id, item)

    @staticmethod
    def _current_actions(
        item: BatchItemRecord,
        target: DocumentRecord | None,
    ) -> set[str]:
        if target is not None:
            return {BatchItemDecision.UPDATE.value, BatchItemDecision.SKIP.value}
        if _has_remote_binding(item.remote_binding_json):
            return {
                BatchItemDecision.ATTACH_REMOTE.value,
                BatchItemDecision.SKIP.value,
            }
        return {BatchItemDecision.CREATE.value, BatchItemDecision.SKIP.value}

    def reserve_child_job(self, item_id: str, import_job_id: str) -> BatchItemRecord:
        with self.database.session() as session:
            item = session.get(BatchItemRecord, item_id)
            if item is None:
                raise DomainError("BATCH_ITEM_NOT_FOUND", "批次项不存在", 404)
            ensure_item_mutable(item.state)
            if item.import_job_id is not None:
                raise DomainError("BATCH_ITEM_ALREADY_RESERVED", "批次项已绑定子任务", 409)
            if not item.selected or item.decision is None:
                raise DomainError("BATCH_STATE_CONFLICT", "批次项尚未确认", 409)
            if session.get(ImportJobRecord, import_job_id) is None:
                raise DomainError("BATCH_ITEM_JOB_NOT_FOUND", "子任务不存在", 409)
            item.import_job_id = import_job_id
            item.state = BatchItemState.QUEUED
            item.updated_at = utc_now()
            session.flush()
            return item

    def set_state(
        self, batch_id: str, target: BatchState, *, message: str | None = None
    ) -> BatchImportRecord:
        with self.database.session() as session:
            batch = session.get(BatchImportRecord, batch_id)
            if batch is None:
                raise DomainError("BATCH_NOT_FOUND", "批次不存在", 404)
            if batch.state != target:
                transition(batch.state, target)
                batch.state = target
                if target == BatchState.RUNNING and batch.started_at is None:
                    batch.started_at = utc_now()
                if target in {
                    BatchState.COMPLETED,
                    BatchState.COMPLETED_WITH_ERRORS,
                    BatchState.FAILED,
                    BatchState.CANCELLED,
                }:
                    batch.completed_at = utc_now()
            if message is not None:
                batch.message = message
            batch.updated_at = utc_now()
            session.flush()
            return batch

    def request_cancel(self, batch_id: str) -> BatchImportRecord:
        with self.database.session() as session:
            batch = session.get(BatchImportRecord, batch_id)
            if batch is None:
                raise DomainError("BATCH_NOT_FOUND", "批次不存在", 404)
            if batch.state in {
                BatchState.COMPLETED,
                BatchState.COMPLETED_WITH_ERRORS,
                BatchState.FAILED,
                BatchState.CANCELLED,
            }:
                return batch
            batch.cancel_requested = True
            batch.updated_at = utc_now()
            session.flush()
            return batch

    def mark_item_running(self, item_id: str) -> BatchItemRecord:
        with self.database.session() as session:
            item = session.get(BatchItemRecord, item_id)
            if item is None:
                raise DomainError("BATCH_ITEM_NOT_FOUND", "批次项不存在", 404)
            if item.state == BatchItemState.RUNNING:
                return item
            ensure_item_mutable(item.state)
            if item.state != BatchItemState.QUEUED:
                raise DomainError("BATCH_STATE_CONFLICT", "批次项未排队", 409)
            item.state = BatchItemState.RUNNING
            item.updated_at = utc_now()
            session.flush()
            return item

    def mark_item_cancelled(self, item_id: str) -> BatchItemRecord:
        with self.database.session() as session:
            item = session.get(BatchItemRecord, item_id)
            if item is None:
                raise DomainError("BATCH_ITEM_NOT_FOUND", "批次项不存在", 404)
            if item.state not in {
                BatchItemState.DISCOVERED,
                BatchItemState.QUEUED,
                BatchItemState.RUNNING,
            }:
                return item
            item.state = BatchItemState.CANCELLED
            item.updated_at = utc_now()
            session.flush()
            return item

    def finish_cancel(self, batch_id: str) -> BatchImportRecord:
        """Make selected item cancellation terminal before the parent transition."""
        with self.database.session() as session:
            batch = session.get(BatchImportRecord, batch_id)
            if batch is None:
                raise DomainError("BATCH_NOT_FOUND", "批次不存在", 404)
            if batch.state in {
                BatchState.COMPLETED,
                BatchState.COMPLETED_WITH_ERRORS,
                BatchState.FAILED,
                BatchState.CANCELLED,
            }:
                return batch
            now = utc_now()
            items = list(
                session.scalars(select(BatchItemRecord).where(BatchItemRecord.batch_id == batch_id))
            )
            for item in items:
                if item.selected and item.state in {
                    BatchItemState.DISCOVERED,
                    BatchItemState.QUEUED,
                    BatchItemState.RUNNING,
                }:
                    item.state = BatchItemState.CANCELLED
                    item.updated_at = now
            transition(batch.state, BatchState.CANCELLED)
            batch.state = BatchState.CANCELLED
            batch.completed_at = now
            batch.total_count = len(items)
            batch.selected_count = sum(item.selected for item in items)
            batch.completed_count = sum(item.state == BatchItemState.COMPLETED for item in items)
            batch.failed_count = sum(item.state == BatchItemState.FAILED for item in items)
            batch.skipped_count = sum(item.state == BatchItemState.SKIPPED for item in items)
            finished = sum(
                item.state
                in {
                    BatchItemState.COMPLETED,
                    BatchItemState.FAILED,
                    BatchItemState.SKIPPED,
                    BatchItemState.CANCELLED,
                }
                for item in items
            )
            batch.progress = int(finished * 100 / len(items)) if items else 100
            batch.message = "批次已取消"
            batch.updated_at = now
            session.flush()
            return batch

    def mark_failed(
        self, item_id: str, *, code: str, message: str, retryable: bool
    ) -> BatchItemRecord:
        with self.database.session() as session:
            item = session.get(BatchItemRecord, item_id)
            if item is None:
                raise DomainError("BATCH_ITEM_NOT_FOUND", "批次项不存在", 404)
            if item.state in {
                BatchItemState.COMPLETED,
                BatchItemState.SKIPPED,
                BatchItemState.CANCELLED,
            }:
                return item
            item.state = BatchItemState.FAILED
            item.error_code = code
            item.error_message = message
            item.retryable = retryable
            item.updated_at = utc_now()
            session.flush()
            return item

    def mark_item_queued(self, item_id: str) -> BatchItemRecord:
        with self.database.session() as session:
            item = session.get(BatchItemRecord, item_id)
            if item is None:
                raise DomainError("BATCH_ITEM_NOT_FOUND", "批次项不存在", 404)
            if item.state in {
                BatchItemState.COMPLETED,
                BatchItemState.SKIPPED,
                BatchItemState.CANCELLED,
            }:
                raise DomainError("BATCH_STATE_CONFLICT", "批次项已完成，无法重试", 409)
            item.state = BatchItemState.QUEUED
            item.error_code = None
            item.error_message = None
            item.retryable = False
            item.updated_at = utc_now()
            session.flush()
            return item

    def sync_child_terminal(self, item_id: str, child: ImportJobRecord | Any) -> BatchItemRecord:
        state_value = getattr(child, "state", None)
        if isinstance(state_value, str):
            state_value = ImportStatus(state_value)
        with self.database.session() as session:
            item = session.get(BatchItemRecord, item_id)
            if item is None:
                raise DomainError("BATCH_ITEM_NOT_FOUND", "批次项不存在", 404)
            if item.state in {
                BatchItemState.COMPLETED,
                BatchItemState.SKIPPED,
                BatchItemState.CANCELLED,
            }:
                return item
            if state_value == ImportStatus.COMPLETED:
                item.state = BatchItemState.COMPLETED
            elif state_value == ImportStatus.CANCELLED:
                item.state = BatchItemState.CANCELLED
            elif state_value == ImportStatus.FAILED:
                item.state = BatchItemState.FAILED
                item.error_code = getattr(child, "error_code", None)
                item.error_message = getattr(child, "error_message", None)
                item.retryable = bool(getattr(child, "retryable", False))
            else:
                return item
            item.updated_at = utc_now()
            session.flush()
            return item

    def update_counts(
        self,
        batch_id: str,
        *,
        total_count: int | None = None,
        selected_count: int | None = None,
        completed_count: int | None = None,
        failed_count: int | None = None,
        skipped_count: int | None = None,
        progress: int | None = None,
        message: str | None = None,
    ) -> BatchImportRecord:
        values = {
            "total_count": total_count,
            "selected_count": selected_count,
            "completed_count": completed_count,
            "failed_count": failed_count,
            "skipped_count": skipped_count,
            "progress": progress,
            "message": message,
        }
        if any(
            value is not None and isinstance(value, int) and value < 0 for value in values.values()
        ):
            raise ValueError("batch counts and progress must be non-negative")
        if progress is not None and progress > 100:
            raise ValueError("progress must be at most 100")
        with self.database.session() as session:
            batch = session.get(BatchImportRecord, batch_id)
            if batch is None:
                raise DomainError("BATCH_NOT_FOUND", "批次不存在", 404)
            for field, value in values.items():
                if value is not None:
                    setattr(batch, field, value)
            batch.updated_at = utc_now()
            session.flush()
            return batch

    def allocate_event_sequence(self, batch_id: str) -> int:
        with self.database.session() as session:
            sequence = session.scalar(
                update(BatchImportRecord)
                .where(BatchImportRecord.id == batch_id)
                .values(
                    last_event_sequence=BatchImportRecord.last_event_sequence + 1,
                    updated_at=utc_now(),
                )
                .returning(BatchImportRecord.last_event_sequence)
            )
            if sequence is None:
                raise DomainError("BATCH_NOT_FOUND", "批次不存在", 404)
            return int(sequence)

    def recover_on_startup(self) -> int:
        with self.database.session() as session:
            batches = list(session.scalars(select(BatchImportRecord)))
            now = utc_now()
            recovered = 0
            for batch in batches:
                if batch.state == BatchState.DISCOVERING:
                    batch.state = BatchState.FAILED
                    batch.error_code = "BATCH_APP_RESTARTED"
                    batch.error_message = "应用重启导致目录发现中断"
                    batch.retryable = True
                    batch.message = "应用重启导致目录发现中断"
                    batch.completed_at = now
                    batch.updated_at = now
                    recovered += 1
                elif batch.state == BatchState.RUNNING:
                    batch.state = BatchState.PAUSED
                    batch.message = "应用重启导致批次暂停"
                    batch.updated_at = now
                    recovered += 1
                if batch.state == BatchState.PAUSED:
                    active_items = session.scalars(
                        select(BatchItemRecord).where(
                            BatchItemRecord.batch_id == batch.id,
                            BatchItemRecord.state == BatchItemState.RUNNING,
                        )
                    )
                    for item in active_items:
                        item.state = BatchItemState.QUEUED
                        item.updated_at = now
            return recovered


class CrawlEntryStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def upsert_frontier(self, entry: CrawlEntryRecord) -> CrawlEntryRecord:
        # The unique (batch_id, canonical_url) constraint is the source of
        # truth under concurrent discovery. Two workers may both observe no
        # row; let one insert win, then resolve the loser to the committed row.
        try:
            with self.database.session() as session:
                existing = session.scalar(
                    select(CrawlEntryRecord).where(
                        CrawlEntryRecord.batch_id == entry.batch_id,
                        CrawlEntryRecord.canonical_url == entry.canonical_url,
                    )
                )
                if existing:
                    return existing
                session.add(entry)
                session.flush()
                return entry
        except IntegrityError:
            with self.database.session() as session:
                existing = session.scalar(
                    select(CrawlEntryRecord).where(
                        CrawlEntryRecord.batch_id == entry.batch_id,
                        CrawlEntryRecord.canonical_url == entry.canonical_url,
                    )
                )
                if existing:
                    return existing
            raise

    def claim(self, batch_id: str) -> CrawlEntryRecord | None:
        with self.database.session() as session:
            # Select a deterministic candidate, then condition the UPDATE on
            # the still-pending state. If another worker won the race, retry
            # against the next pending entry rather than returning a duplicate.
            while True:
                candidate = session.scalar(
                    select(CrawlEntryRecord)
                    .where(
                        CrawlEntryRecord.batch_id == batch_id,
                        CrawlEntryRecord.state == CrawlEntryState.PENDING,
                    )
                    .order_by(CrawlEntryRecord.depth, CrawlEntryRecord.id)
                    .limit(1)
                )
                if candidate is None:
                    return None
                now = datetime.now(UTC)
                result = session.execute(
                    update(CrawlEntryRecord)
                    .where(
                        CrawlEntryRecord.id == candidate.id,
                        CrawlEntryRecord.state == CrawlEntryState.PENDING,
                    )
                    .values(state=CrawlEntryState.FETCHING, updated_at=now)
                )
                if result.rowcount:
                    session.expire(candidate)
                    return session.get(CrawlEntryRecord, candidate.id)

    def mark_fetched(self, entry_id: str, **kwargs: Any) -> CrawlEntryRecord | None:
        # Persist completion time for recovery/audit; callers may still supply
        # an explicit timestamp (e.g. deterministic tests).
        kwargs.setdefault("fetched_at", utc_now())
        return self._mark(entry_id, CrawlEntryState.FETCHED, **kwargs)

    def mark_rejected(self, entry_id: str, **kwargs: Any) -> CrawlEntryRecord | None:
        return self._mark(entry_id, CrawlEntryState.REJECTED, **kwargs)

    def canonical_urls(self, *, limit: int = 500) -> list[str]:
        """最近抓取/尝试抓取过的地址。

        取"尝试过"而不只是"成功过"：用户试过抓一个站，就说明他对那个站有兴趣 ——
        而当时失败的原因（被墙、被限流）正是本地语料想去解决的。
        """
        with self.database.session() as session:
            rows = session.scalars(
                select(CrawlEntryRecord.canonical_url)
                .order_by(CrawlEntryRecord.created_at.desc(), CrawlEntryRecord.id)
                .limit(limit)
            )
        return list(rows)

    def rejections(self, batch_id: str, *, limit: int = 200) -> tuple[int, list[CrawlEntryRecord]]:
        """该批次被拒的条目，返回 ``(总数, 前 limit 条)``。

        总数单独查：列表会被 ``limit`` 截断，而"一共有多少页没进来"正是用户第一眼
        要的那个数字 —— 用截断后的长度冒充它，会让一个 500 条拒绝的批次看起来只
        有 200 条。
        """
        conditions = (
            CrawlEntryRecord.batch_id == batch_id,
            CrawlEntryRecord.state == CrawlEntryState.REJECTED,
        )
        with self.database.session() as session:
            total = session.scalar(
                select(func.count()).select_from(CrawlEntryRecord).where(*conditions)
            )
            rows = list(
                session.scalars(
                    select(CrawlEntryRecord)
                    .where(*conditions)
                    .order_by(CrawlEntryRecord.canonical_url)
                    .limit(limit)
                )
            )
        return int(total or 0), rows

    def _mark(
        self, entry_id: str, state: CrawlEntryState, **kwargs: Any
    ) -> CrawlEntryRecord | None:
        with self.database.session() as session:
            row = session.get(CrawlEntryRecord, entry_id)
            if row is None:
                return None
            row.state = state
            row.updated_at = datetime.now(UTC)
            for key, value in kwargs.items():
                setattr(row, key, value)
            session.flush()
            return row

    def release_fetching_on_restart(self, batch_id: str) -> int:
        with self.database.session() as session:
            result = session.execute(
                update(CrawlEntryRecord)
                .where(
                    CrawlEntryRecord.batch_id == batch_id,
                    CrawlEntryRecord.state == CrawlEntryState.FETCHING,
                )
                .values(
                    state=CrawlEntryState.FAILED,
                    error_code="APP_RESTARTED",
                    updated_at=datetime.now(UTC),
                )
            )
            return result.rowcount


def _source_descriptor_json(value: Any) -> str:
    return _encode_json(value, expected=dict, default={})


def _cached_source_json(value: Any) -> str:
    return _encode_json(value, expected=dict, default={})


def _allowed_actions_json(value: Any) -> str:
    return _encode_json(value, expected=list, default=[])


def _encode_json(value: Any, *, expected: type, default: Any) -> str:
    decoded = default if value is None else value
    if isinstance(decoded, str):
        try:
            decoded = json.loads(decoded)
        except json.JSONDecodeError as error:
            raise ValueError("batch JSON must be valid") from error
    if not isinstance(decoded, expected):
        raise TypeError(f"batch JSON must encode a {expected.__name__}")
    return json.dumps(decoded, ensure_ascii=False, separators=(",", ":"))


def _batch_item_view(item: BatchItemRecord) -> BatchItemView:
    allowed_actions = _decode_allowed_actions(item.allowed_actions_json)
    return BatchItemView(
        id=item.id,
        batch_id=item.batch_id,
        ordinal=item.ordinal,
        title=item.title,
        display_path=item.display_path,
        media_type=item.media_type,
        size_bytes=item.size_bytes,
        source_revision=item.source_revision,
        allowed_actions=allowed_actions,
        selected=item.selected,
        decision=item.decision,
        state=item.state,
        import_job_id=item.import_job_id,
        error_code=item.error_code,
        error_message=item.error_message,
        retryable=item.retryable,
    )


def _decode_allowed_actions(value: str) -> list[str]:
    allowed_actions = json.loads(value)
    if not isinstance(allowed_actions, list) or not all(
        isinstance(action, str) for action in allowed_actions
    ):
        raise ValueError("allowed_actions_json must encode a list of strings")
    return allowed_actions


def _has_remote_binding(value: str | None) -> bool:
    if value is None:
        return False
    try:
        binding = json.loads(value)
    except json.JSONDecodeError:
        return False
    if not isinstance(binding, dict):
        return False
    repository_id = binding.get("repository_id", binding.get("repositoryId"))
    document_id = binding.get("document_id", binding.get("documentId"))
    return (
        isinstance(repository_id, str)
        and bool(repository_id.strip())
        and isinstance(document_id, str)
        and bool(document_id.strip())
    )


def _encode_batch_item_cursor(item: BatchItemRecord) -> str:
    value = f"{item.ordinal}:{item.id}".encode()
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode_batch_item_cursor(cursor: str) -> tuple[int, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        ordinal_text, item_id = base64.urlsafe_b64decode(padded.encode()).decode().split(":", 1)
        return int(ordinal_text), item_id
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("invalid batch item cursor") from error


def _source_fingerprint(source_value: str) -> str | None:
    try:
        metadata = json.loads(source_value)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(metadata, dict):
        return None
    fingerprint = metadata.get("fingerprint")
    return fingerprint if isinstance(fingerprint, str) else None


def _normalized_source_metadata(job: ImportJobRecord) -> dict[str, Any]:
    try:
        decoded = json.loads(job.source_value)
    except (json.JSONDecodeError, TypeError):
        decoded = None
    if (
        isinstance(decoded, dict)
        and decoded.get("version") in {1, 2}
        and isinstance(decoded.get("value"), str)
    ):
        metadata = dict(decoded)
        value = decoded["value"]
    else:
        metadata = {}
        value = job.source_value
    fingerprint = metadata.get("fingerprint")
    duplicate_decision = metadata.get("duplicate_decision")
    intent = metadata.get("upload_intent")
    marker = (
        intent["marker"]
        if isinstance(intent, dict) and isinstance(intent.get("marker"), str)
        else f"docmind-import:{job.id}"
    )
    last_event_sequence = metadata.get("last_event_sequence", 0)
    if (
        not isinstance(last_event_sequence, int)
        or isinstance(last_event_sequence, bool)
        or last_event_sequence < 0
    ):
        last_event_sequence = 0
    stale_vector_ids = metadata.get("stale_vector_ids", [])
    if not isinstance(stale_vector_ids, list) or not all(
        isinstance(identifier, str) for identifier in stale_vector_ids
    ):
        stale_vector_ids = []
    pending_created_vector_ids = metadata.get("pending_created_vector_ids", [])
    if not isinstance(pending_created_vector_ids, list) or not all(
        isinstance(identifier, str) for identifier in pending_created_vector_ids
    ):
        pending_created_vector_ids = []
    return {
        **metadata,
        "version": 2,
        "value": value,
        "fingerprint": fingerprint if isinstance(fingerprint, str) else "",
        "duplicate_decision": (
            duplicate_decision if isinstance(duplicate_decision, str) else "create"
        ),
        "upload_intent": {"marker": marker},
        "last_event_sequence": last_event_sequence,
        "stale_vector_ids": list(dict.fromkeys(stale_vector_ids)),
        "pending_created_vector_ids": list(dict.fromkeys(pending_created_vector_ids)),
    }


def _dump_source_metadata(metadata: dict[str, Any]) -> str:
    return json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))


class WebSourceStore:
    """用户决定纳入本地网页索引的来源。

    只读写 `web_sources`：候选怎么推导、抓取怎么进行都不在这里 —— 它的职责是
    "把用户定下来的那份范围存住"，而已收录来源是后续抓取与索引的唯一输入。
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    def list(self) -> list[WebSourceRecord]:
        """已收录的来源，最近加入的在前。

        并列时按域名排序：同一批批量加入的记录时间戳相同，没有次级键的话每次刷新
        顺序都可能变，而用户会以为列表在乱动。
        """
        with self.database.session() as session:
            return list(
                session.scalars(
                    select(WebSourceRecord).order_by(
                        WebSourceRecord.added_at.desc(), WebSourceRecord.domain.asc()
                    )
                )
            )

    def upsert(self, sources: Iterable[WebSourceRecord]) -> list[WebSourceRecord]:
        """加入或更新来源，返回写入后的完整清单。

        幂等由主键保证（domain 就是主键）。重复加入时**合并证据而不是覆盖**：一个
        域名可能先因为种子表被收录、后来又因为文档引用被再次加入，两条理由都值得留着
        —— 只留最新的一条会让用户下次回来看时以为它只有一个来路。
        """
        with self.database.session() as session:
            for source in sources:
                existing = session.get(WebSourceRecord, source.domain)
                if existing is None:
                    session.add(source)
                    continue
                merged = list(
                    dict.fromkeys(
                        [*json.loads(existing.evidence_json or "[]"), *json.loads(source.evidence_json or "[]")]
                    )
                )
                existing.evidence_json = json.dumps(merged, ensure_ascii=False)
                existing.updated_at = utc_now()
                # 元数据只在有值时更新：一次只带域名的"手工添加"不该把种子表带来的
                # 显示名与抓取入口抹掉。
                if source.entry_url:
                    existing.entry_url = source.entry_url
                if source.label:
                    existing.label = source.label
                if source.kind:
                    existing.kind = source.kind
                # `bool(...)` 不是多余的：未显式赋值的 ORM 对象在 flush 之前字段是 None，
                # 而 `False or None` 会把 None 写进 NOT NULL 列（这条曾被测试抓到）。
                existing.verified = bool(existing.verified or source.verified)
            session.flush()
        return self.list()

    def mark_crawl_started(self, domain: str, *, page_budget: int) -> bool:
        """把来源标成"正在抓取"。已经在抓的返回 False（重复点击不该起第二个任务）。"""
        with self.database.session() as session:
            row = session.get(WebSourceRecord, domain)
            if row is None or row.crawl_state == CrawlState.RUNNING.value:
                return False
            row.crawl_state = CrawlState.RUNNING.value
            row.crawl_progress_json = json.dumps({"pages": 0, "updated": 0, "budget": page_budget})
            row.updated_at = utc_now()
            return True

    def mark_crawl_progress(self, domain: str, *, pages: int, updated: int) -> None:
        with self.database.session() as session:
            row = session.get(WebSourceRecord, domain)
            if row is None:
                return
            budget = json.loads(row.crawl_progress_json or "{}").get("budget", 0)
            row.crawl_progress_json = json.dumps({"pages": pages, "updated": updated, "budget": budget})

    def mark_crawl_finished(
        self, domain: str, *, error: str | None, summary: dict, interrupted: bool = False
    ) -> None:
        """抓取结束。进度清空、摘要留下。

        `interrupted=True` 用于**没有正常跑完**的两种情况：应用退出、或抓取内部出错。
        它们与"抓完了一页都没抓到"是两回事，而后者有 `last_error` 可以表达；把内部
        出错记成普通的 idle 会让一个程序缺陷看起来像"这个站没内容"。
        """
        with self.database.session() as session:
            row = session.get(WebSourceRecord, domain)
            if row is None:
                return
            row.crawl_state = (
                CrawlState.INTERRUPTED.value if interrupted else CrawlState.IDLE.value
            )
            row.crawl_progress_json = "{}"
            row.last_summary_json = json.dumps(summary, ensure_ascii=False)
            row.last_crawled_at = utc_now()
            row.last_error = error
            row.updated_at = utc_now()

    def recover_interrupted_crawls(self) -> int:
        """应用启动时把"正在抓取"改判为"被中断"。

        没有这一步，一次应用崩溃会留下一个永远显示"正在抓取"的来源 —— 而那个任务
        早已不存在，用户只能干等或删掉重加。
        """
        with self.database.session() as session:
            rows = list(
                session.scalars(
                    select(WebSourceRecord).where(
                        WebSourceRecord.crawl_state == CrawlState.RUNNING.value
                    )
                )
            )
            for row in rows:
                row.crawl_state = CrawlState.INTERRUPTED.value
                row.crawl_progress_json = "{}"
                row.updated_at = utc_now()
            return len(rows)

    def mark_crawled(self, domain: str, error: str | None) -> None:
        """记下这次抓取的时间与结果。

        成功时把 `last_error` 清掉：一个来源曾经失败、后来抓好了，界面上还挂着旧错误
        会让人以为它一直没修好。
        """
        with self.database.session() as session:
            row = session.get(WebSourceRecord, domain)
            if row is None:
                return
            row.last_crawled_at = utc_now()
            row.last_error = error
            row.updated_at = utc_now()

    def remove(self, domain: str) -> bool:
        with self.database.session() as session:
            row = session.get(WebSourceRecord, domain)
            if row is None:
                return False
            session.delete(row)
            return True


@dataclass(frozen=True)
class CorpusStats:
    """一个域名的语料规模与最近一次抓取时间。界面用它回答"到底抓到了没有"。"""

    pages: int
    chunks: int
    last_fetched_at: datetime | None


@dataclass(frozen=True)
class CorpusPageWrite:
    """这次抓取对某一页的结论。

    `chunks is None` 表示**内容未变**：只更新抓取时间与校验器，原有的块一个都不动。
    这就是增量刷新的意义 —— 没变的页面不重新切块、不重写索引，块的 id 也就不会变。
    """

    page: WebCorpusPageRecord
    chunks: tuple[WebCorpusChunkRecord, ...] | None


class WebCorpusStore:
    """本地网页语料的读写。

    抓取按**域名整体替换**：先删该域名的旧页与旧块，再写入这一次的结果。比起逐页
    去重，这样"页面被删掉了""改版后内容变了"都会自动反映出来，代价是每次抓取都要
    重写该域名的全部块 —— 在一个来源几十上百页的规模上，这个代价是划算的，而且它把
    "索引里混着上一版残留"这类问题整个消掉。增量刷新（ETag / 内容 hash 比对）留到
    下一步。
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    def replace_domain(
        self,
        domain: str,
        pages: list[WebCorpusPageRecord],
        chunks: list[WebCorpusChunkRecord],
    ) -> None:
        with self.database.session() as session:
            session.execute(
                delete(WebCorpusChunkRecord).where(WebCorpusChunkRecord.domain == domain)
            )
            session.execute(delete(WebCorpusPageRecord).where(WebCorpusPageRecord.domain == domain))
            session.add_all(pages)
            session.flush()
            session.add_all(chunks)

    def pages(self, domain: str) -> list[WebCorpusPageRecord]:
        """该域名已有的页面 —— 抓取前用它判断"这一页变了没有"。"""
        with self.database.session() as session:
            return list(
                session.scalars(
                    select(WebCorpusPageRecord).where(WebCorpusPageRecord.domain == domain)
                )
            )

    def apply_crawl(
        self,
        domain: str,
        writes: Sequence[CorpusPageWrite],
        *,
        removed_urls: Sequence[str] = (),
    ) -> list[str]:
        """按这次抓取的结论更新语料，而不是整域重写。

        `removed_urls` 只该装**有证据表明已经消失**的页面（404 / 410）。没有证据的
        一律不动：一次超时、一次被限流、或抓取被预算截断，都不代表页面不存在 ——
        把不确定当成不存在，会让语料在一次网络抖动后凭空缩水。

        返回**被删掉的块 id**：向量索引按块 id 存的，语料删了而向量不删，就会留下
        悬空向量 —— 它们占着空间，而且会让"向量找回来的块"在取正文时凭空消失。
        """
        now = utc_now()
        removed_chunk_ids: list[str] = []
        with self.database.session() as session:
            for url in removed_urls:
                removed_chunk_ids.extend(
                    session.scalars(
                        select(WebCorpusChunkRecord.id).where(
                            WebCorpusChunkRecord.domain == domain, WebCorpusChunkRecord.url == url
                        )
                    )
                )
            for write in writes:
                if write.chunks is None:
                    continue
                removed_chunk_ids.extend(
                    session.scalars(
                        select(WebCorpusChunkRecord.id).where(
                            WebCorpusChunkRecord.domain == domain,
                            WebCorpusChunkRecord.url == write.page.url,
                        )
                    )
                )
            for url in removed_urls:
                session.execute(
                    delete(WebCorpusChunkRecord).where(
                        WebCorpusChunkRecord.domain == domain, WebCorpusChunkRecord.url == url
                    )
                )
                session.execute(
                    delete(WebCorpusPageRecord).where(
                        WebCorpusPageRecord.domain == domain, WebCorpusPageRecord.url == url
                    )
                )
            for write in writes:
                conditions = (
                    WebCorpusPageRecord.domain == domain,
                    WebCorpusPageRecord.url == write.page.url,
                )
                if write.chunks is None:
                    session.execute(
                        update(WebCorpusPageRecord)
                        .where(*conditions)
                        .values(
                            fetched_at=now,
                            etag=write.page.etag,
                            last_modified=write.page.last_modified,
                        )
                    )
                    continue
                session.execute(
                    delete(WebCorpusChunkRecord).where(
                        WebCorpusChunkRecord.domain == domain,
                        WebCorpusChunkRecord.url == write.page.url,
                    )
                )
                session.execute(delete(WebCorpusPageRecord).where(*conditions))
                session.add(write.page)
                session.flush()
                session.add_all(list(write.chunks))
        return removed_chunk_ids

    def chunks_by_ids(self, ids: Sequence[str]) -> dict[str, WebCorpusChunkRecord]:
        """按 id 取块。检索融合之后拿到的是 id（向量通道只给 id），取正文要回来查一次。

        查不到的 id 直接缺席 —— 悬空向量（索引里有、语料里没有）因此自然被过滤掉，
        而不是报错。
        """
        if not ids:
            return {}
        with self.database.session() as session:
            rows = session.scalars(
                select(WebCorpusChunkRecord).where(WebCorpusChunkRecord.id.in_(list(ids)))
            )
            return {row.id: row for row in rows}

    def chunks(self, domains: list[str] | None = None) -> list[WebCorpusChunkRecord]:
        with self.database.session() as session:
            statement = select(WebCorpusChunkRecord)
            if domains:
                statement = statement.where(WebCorpusChunkRecord.domain.in_(domains))
            return list(session.scalars(statement))

    def stats(self) -> dict[str, CorpusStats]:
        """全部域名的 (页数, 块数, 最近抓取时间)。

        一次分组查询而不是每行查一次：来源列表本来就会整体渲染，按行查会变成 N 次查询。
        """
        with self.database.session() as session:
            pages = dict(
                session.execute(
                    select(WebCorpusPageRecord.domain, func.count()).group_by(
                        WebCorpusPageRecord.domain
                    )
                ).all()
            )
            chunks = dict(
                session.execute(
                    select(WebCorpusChunkRecord.domain, func.count()).group_by(
                        WebCorpusChunkRecord.domain
                    )
                ).all()
            )
            latest = dict(
                session.execute(
                    select(
                        WebCorpusPageRecord.domain, func.max(WebCorpusPageRecord.fetched_at)
                    ).group_by(WebCorpusPageRecord.domain)
                ).all()
            )
        domains = set(pages) | set(chunks) | set(latest)
        return {
            domain: CorpusStats(
                pages=int(pages.get(domain, 0)),
                chunks=int(chunks.get(domain, 0)),
                last_fetched_at=latest.get(domain),
            )
            for domain in domains
        }

    def clear_domain(self, domain: str) -> list[str]:
        """清掉一个域名的全部语料，返回被删的块 id（供向量索引同步清理）。"""
        with self.database.session() as session:
            removed = list(
                session.scalars(
                    select(WebCorpusChunkRecord.id).where(WebCorpusChunkRecord.domain == domain)
                )
            )
        self.replace_domain(domain, [], [])
        return removed


class ConversationStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_session(
        self, repository_ids: list[str], *, title: str | None = None
    ) -> SessionRecord:
        with self.database.session() as session:
            record = SessionRecord(title=title, repository_scope_json=json.dumps(repository_ids))
            session.add(record)
            session.flush()
            return record

    def get_session(self, session_id: str) -> SessionRecord | None:
        with self.database.session() as session:
            return session.get(SessionRecord, session_id)

    def list_sessions(self) -> list[SessionRecord]:
        with self.database.session() as session:
            return list(
                session.scalars(select(SessionRecord).order_by(SessionRecord.updated_at.desc()))
            )

    def list_messages(self, session_id: str) -> list[MessageRecord]:
        with self.database.session() as session:
            statement = (
                select(MessageRecord)
                .where(MessageRecord.session_id == session_id)
                .order_by(MessageRecord.created_at.asc())
            )
            return list(session.scalars(statement))

    def get_message(self, message_id: str) -> MessageRecord | None:
        with self.database.session() as session:
            return session.get(MessageRecord, message_id)

    def add_message(self, message: MessageRecord) -> MessageRecord:
        with self.database.session() as session:
            parent = session.get(SessionRecord, message.session_id)
            if parent is not None and parent.ended_at is not None:
                raise DomainError("SESSION_ENDED", "会话已结束", 409)
            session.add(message)
            parent = session.get(SessionRecord, message.session_id)
            if parent is not None:
                parent.updated_at = utc_now()
            session.flush()
            return message

    def mark_message_activity(self, session_id: str) -> None:
        from datetime import timedelta

        with self.database.session() as session:
            parent = session.get(SessionRecord, session_id)
            if parent is not None:
                parent.updated_at = utc_now()
                parent.summary_due_at = utc_now() + timedelta(minutes=30)

    def end_session(self, session_id: str) -> SessionRecord:
        with self.database.session() as session:
            parent = session.get(SessionRecord, session_id)
            if parent is None:
                raise DomainError("SESSION_NOT_FOUND", "会话不存在", 404)
            if parent.ended_at is None:
                parent.ended_at = utc_now()
            return parent

    def delete_session(self, session_id: str, *, confirm: bool = False) -> None:
        if not confirm:
            raise DomainError("CONFIRM_REQUIRED", "需要确认", 400)
        with self.database.session() as session:
            parent = session.get(SessionRecord, session_id)
            if parent is not None:
                summary_ids = list(
                    session.scalars(
                        select(SessionSummaryRecord.id).where(
                            SessionSummaryRecord.session_id == session_id
                        )
                    )
                )
                draft_ids = list(
                    session.scalars(
                        select(DistillationRecord.id).where(
                            DistillationRecord.session_id == session_id,
                            DistillationRecord.state.not_in(("saved", "saved_unindexed")),
                        )
                    )
                )
                summary_vectors = (
                    list(
                        session.scalars(
                            select(MemoryChunkRecord.vector_id).where(
                                MemoryChunkRecord.summary_id.in_(summary_ids)
                            )
                        )
                    )
                    if summary_ids
                    else []
                )
                draft_vectors = (
                    list(
                        session.scalars(
                            select(MemoryChunkRecord.vector_id).where(
                                MemoryChunkRecord.distillation_id.in_(draft_ids)
                            )
                        )
                    )
                    if draft_ids
                    else []
                )
                if summary_vectors:
                    session.add(
                        MemoryVectorCleanupRecord(
                            collection="_session_summaries",
                            vector_ids_json=json.dumps(summary_vectors),
                        )
                    )
                if draft_vectors:
                    session.add(
                        MemoryVectorCleanupRecord(
                            collection="_distilled_knowledge",
                            vector_ids_json=json.dumps(draft_vectors),
                        )
                    )
                session.query(DistillationRecord).filter(
                    DistillationRecord.session_id == session_id,
                    DistillationRecord.state.in_(("saved", "saved_unindexed")),
                ).update({DistillationRecord.session_id: None}, synchronize_session=False)
                session.query(DistillationRecord).filter(
                    DistillationRecord.session_id == session_id
                ).delete(synchronize_session=False)
                session.delete(parent)

    def claim_chat_request(
        self, request_id: str, session_id: str
    ) -> tuple[ChatRequestRecord, bool]:
        """Atomically reserve a chat request ID for exactly one producer."""
        with self.database.session() as session:
            result = session.execute(
                sqlite_insert(ChatRequestRecord)
                .values(request_id=request_id, session_id=session_id)
                .on_conflict_do_nothing(index_elements=["request_id"])
            )
            record = session.get(ChatRequestRecord, request_id)
            if record is None:
                raise DomainError("CHAT_REQUEST_CONFLICT", "聊天请求状态冲突", 409)
            return record, result.rowcount == 1

    def complete_chat_request(
        self, request_id: str, terminal_type: str, payload: dict[str, Any]
    ) -> ChatRequestRecord:
        with self.database.session() as session:
            record = session.get(ChatRequestRecord, request_id)
            if record is None:
                raise DomainError("CHAT_REQUEST_CONFLICT", "聊天请求状态冲突", 409)
            record.terminal_type = terminal_type
            record.terminal_payload_json = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            )
            record.updated_at = utc_now()
            session.flush()
            return record

    def allocate_distillation_event_sequence(self, distillation_id: str) -> int:
        with self.database.session() as session:
            sequence = session.execute(
                update(DistillationRecord)
                .where(DistillationRecord.id == distillation_id)
                .values(last_event_sequence=DistillationRecord.last_event_sequence + 1)
                .returning(DistillationRecord.last_event_sequence)
            ).scalar_one_or_none()
            if sequence is None:
                raise DomainError("DISTILLATION_NOT_FOUND", "蒸馏不存在", 404)
            return int(sequence)


class VectorCleanupStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(self, repository_id: str, document_id: str, vector_ids: list[str]) -> None:
        with self.database.session() as session:
            session.add(
                VectorCleanupRecord(
                    repository_id=repository_id,
                    document_id=document_id,
                    vector_ids_json=json.dumps(vector_ids),
                )
            )

    def list(self) -> list[VectorCleanupRecord]:
        with self.database.session() as session:
            return list(session.scalars(select(VectorCleanupRecord)))

    def delete(self, cleanup_id: str) -> None:
        with self.database.session() as session:
            record = session.get(VectorCleanupRecord, cleanup_id)
            if record is not None:
                session.delete(record)


class MemoryStore:
    def __init__(self, database: Database):
        self.database = database

    def upsert_summary(self, session_id: str, repository_ids: list[str]):
        with self.database.session() as s:
            if s.get(SessionRecord, session_id) is None:
                raise DomainError("SESSION_NOT_FOUND", "会话不存在", 404)
            rec = s.scalar(
                select(SessionSummaryRecord).where(SessionSummaryRecord.session_id == session_id)
            )
            if rec is None:
                rec = SessionSummaryRecord(
                    session_id=session_id, repository_ids_json=json.dumps(repository_ids)
                )
                s.add(rec)
            else:
                rec.repository_ids_json = json.dumps(repository_ids)
                rec.updated_at = utc_now()
            s.flush()
            return rec

    def get_summary(self, session_id: str):
        with self.database.session() as s:
            return s.scalar(
                select(SessionSummaryRecord).where(SessionSummaryRecord.session_id == session_id)
            )

    def claim_due_summary(self, now: datetime):
        with self.database.session() as s:
            parent = s.scalar(
                select(SessionRecord)
                .where(
                    SessionRecord.summary_due_at.is_not(None), SessionRecord.summary_due_at <= now
                )
                .order_by(SessionRecord.summary_due_at, SessionRecord.id)
                .limit(1)
            )
            if parent is None:
                return None
            summary = s.scalar(
                select(SessionSummaryRecord).where(SessionSummaryRecord.session_id == parent.id)
            )
            if summary is None:
                summary = SessionSummaryRecord(
                    session_id=parent.id, repository_ids_json=parent.repository_scope_json
                )
                s.add(summary)
                s.flush()
            claimed = s.execute(
                update(SessionSummaryRecord)
                .where(
                    SessionSummaryRecord.id == summary.id,
                    SessionSummaryRecord.state != "generating",
                )
                .values(state="generating", error_code=None, retryable=False, updated_at=now)
            )
            if not claimed.rowcount:
                return None
            parent.summary_due_at = None
            s.flush()
            return summary

    def begin_summary(self, session_id: str, now: datetime):
        with self.database.session() as s:
            parent = s.get(SessionRecord, session_id)
            if parent is None:
                raise DomainError("SESSION_NOT_FOUND", "会话不存在", 404)
            summary = s.scalar(
                select(SessionSummaryRecord).where(SessionSummaryRecord.session_id == session_id)
            )
            if summary is None:
                summary = SessionSummaryRecord(
                    session_id=session_id, repository_ids_json=parent.repository_scope_json
                )
                s.add(summary)
            summary.state = "generating"
            summary.error_code = None
            summary.retryable = False
            summary.updated_at = now
            parent.summary_due_at = None
            s.flush()
            return summary

    def claim_summary(self, session_id: str, now: datetime):
        with self.database.session() as s:
            parent = s.get(SessionRecord, session_id)
            if parent is None:
                raise DomainError("SESSION_NOT_FOUND", "会话不存在", 404)
            summary = s.scalar(
                select(SessionSummaryRecord).where(SessionSummaryRecord.session_id == session_id)
            )
            if summary is None:
                summary = SessionSummaryRecord(
                    session_id=session_id, repository_ids_json=parent.repository_scope_json
                )
                s.add(summary)
                s.flush()
            claimed = s.execute(
                update(SessionSummaryRecord)
                .where(
                    SessionSummaryRecord.id == summary.id,
                    SessionSummaryRecord.state != "generating",
                )
                .values(state="generating", error_code=None, retryable=False, updated_at=now)
            )
            if not claimed.rowcount:
                return None
            parent.summary_due_at = None
            s.flush()
            return summary

    def complete_summary(self, summary_id: str, *, content: str, topics: list[str], now: datetime):
        with self.database.session() as s:
            summary = s.get(SessionSummaryRecord, summary_id)
            if summary is None:
                raise DomainError("SUMMARY_NOT_FOUND", "摘要不存在", 404)
            summary.state = "ready"
            summary.content = content
            summary.topics_json = json.dumps(topics, ensure_ascii=False)
            summary.error_code = None
            summary.retryable = False
            summary.updated_at = now
            parent = s.get(SessionRecord, summary.session_id)
            summary.source_updated_at = parent.updated_at if parent is not None else now
            s.flush()
            return summary

    def fail_summary(self, summary_id: str, *, now: datetime):
        with self.database.session() as s:
            summary = s.get(SessionSummaryRecord, summary_id)
            if summary is None:
                raise DomainError("SUMMARY_NOT_FOUND", "摘要不存在", 404)
            summary.state = "failed"
            summary.error_code = "SUMMARY_GENERATION_FAILED"
            summary.retryable = True
            summary.updated_at = now
            parent = s.get(SessionRecord, summary.session_id)
            if parent is not None:
                parent.summary_due_at = now + timedelta(minutes=5)
            s.flush()
            return summary

    def add_memory_chunk(self, summary_id: str, repository_id: str | None, text: str):
        if not repository_id:
            raise DomainError("MEMORY_SCOPE_INVALID", "memory scope required", 400)
        with self.database.session() as s:
            summary = s.get(SessionSummaryRecord, summary_id)
            if summary is None or repository_id not in json.loads(
                summary.repository_ids_json or "[]"
            ):
                raise DomainError("MEMORY_SCOPE_INVALID", "memory scope mismatch", 403)
            rec = MemoryChunkRecord(
                summary_id=summary_id,
                repository_id=repository_id,
                text=text,
                chunk_index=0,
                vector_id=f"memory:session_summary:{summary_id}:{repository_id}:0",
            )
            s.add(rec)
            s.flush()
            return rec

    def replace_memory_chunks(
        self,
        kind: str,
        source_id: str,
        chunks: list[tuple[str, int, str, str]],
        *,
        collection: str | None = None,
    ) -> tuple[list[str], list[MemoryChunkRecord], MemoryVectorCleanupRecord | None]:
        if not chunks:
            raise DomainError("MEMORY_SCOPE_INVALID", "memory scope required", 400)
        foreign_key = "summary_id" if kind == "session_summary" else "distillation_id"
        with self.database.session() as session:
            query = session.query(MemoryChunkRecord).filter(
                getattr(MemoryChunkRecord, foreign_key) == source_id
            )
            old_ids = [record.vector_id for record in query.all()]
            new_ids = {chunk[3] for chunk in chunks}
            stale_ids = [identifier for identifier in old_ids if identifier not in new_ids]
            query.delete(synchronize_session=False)
            records = []
            for repository_id, index, text, vector_id in chunks:
                record = MemoryChunkRecord(
                    repository_id=repository_id,
                    chunk_index=index,
                    text=text,
                    vector_id=vector_id,
                    **{foreign_key: source_id},
                )
                session.add(record)
                records.append(record)
            session.flush()
            cleanup = None
            if stale_ids and collection:
                cleanup = MemoryVectorCleanupRecord(
                    collection=collection,
                    vector_ids_json=json.dumps(stale_ids),
                    source_kind=kind,
                    source_id=source_id,
                )
                session.add(cleanup)
                session.flush()
            return old_ids, records, cleanup

    def create_vector_cleanup(
        self,
        collection: str,
        vector_ids: list[str],
        *,
        source_kind: str | None = None,
        source_id: str | None = None,
    ):
        if not vector_ids:
            return None
        with self.database.session() as session:
            record = MemoryVectorCleanupRecord(
                collection=collection,
                vector_ids_json=json.dumps(vector_ids),
                source_kind=source_kind,
                source_id=source_id,
            )
            session.add(record)
            session.flush()
            return record

    def list_vector_cleanups(self):
        with self.database.session() as session:
            return list(session.scalars(select(MemoryVectorCleanupRecord)))

    def delete_vector_cleanup(self, cleanup_id: str) -> None:
        with self.database.session() as session:
            record = session.get(MemoryVectorCleanupRecord, cleanup_id)
            if record is not None:
                session.delete(record)

    def owned_vector_ids(self, vector_ids: list[str]) -> set[str]:
        if not vector_ids:
            return set()
        with self.database.session() as session:
            return set(
                session.scalars(
                    select(MemoryChunkRecord.vector_id).where(
                        MemoryChunkRecord.vector_id.in_(vector_ids)
                    )
                )
            )

    def mark_memory_chunks_indexed(self, vector_ids: list[str]) -> None:
        with self.database.session() as session:
            session.query(MemoryChunkRecord).filter(
                MemoryChunkRecord.vector_id.in_(vector_ids)
            ).update({MemoryChunkRecord.indexed: True}, synchronize_session=False)

    def pending_memory_sources(self) -> tuple[set[str], set[str]]:
        with self.database.session() as session:
            records = list(
                session.scalars(
                    select(MemoryChunkRecord).where(MemoryChunkRecord.indexed.is_(False))
                )
            )
            return (
                {record.summary_id for record in records if record.summary_id},
                {record.distillation_id for record in records if record.distillation_id},
            )

    def recover_interrupted(self, now: datetime | None = None) -> tuple[int, int]:
        now = now or utc_now()
        with self.database.session() as session:
            summaries = (
                session.query(SessionSummaryRecord)
                .filter(SessionSummaryRecord.state == "generating")
                .update(
                    {
                        SessionSummaryRecord.state: "failed",
                        SessionSummaryRecord.error_code: "SUMMARY_GENERATION_INTERRUPTED",
                        SessionSummaryRecord.retryable: True,
                        SessionSummaryRecord.updated_at: now,
                    },
                    synchronize_session=False,
                )
            )
            distillations = (
                session.query(DistillationRecord)
                .filter(DistillationRecord.state.in_(("generating", "saving")))
                .update(
                    {
                        DistillationRecord.state: "failed",
                        DistillationRecord.error_code: "DISTILLATION_INTERRUPTED",
                        DistillationRecord.retryable: True,
                        DistillationRecord.updated_at: now,
                    },
                    synchronize_session=False,
                )
            )
            return summaries, distillations


class DocumentMutationStore:
    """Small durable store for API mutation compensation intents."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def create(
        self,
        *,
        mutation_id: str | None = None,
        operation: str,
        repository_id: str,
        document_id: str | None,
        payload: dict[str, Any],
    ) -> DocumentMutationRecord:
        with self.database.session() as session:
            record = DocumentMutationRecord(
                id=mutation_id or str(uuid4()),
                operation=operation,
                repository_id=repository_id,
                document_id=document_id,
                payload_json=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            )
            session.add(record)
            session.flush()
            return record

    def list(self) -> list[DocumentMutationRecord]:
        with self.database.session() as session:
            return list(
                session.scalars(
                    select(DocumentMutationRecord).order_by(DocumentMutationRecord.created_at)
                )
            )

    def get(self, mutation_id: str) -> DocumentMutationRecord | None:
        with self.database.session() as session:
            return session.get(DocumentMutationRecord, mutation_id)

    def has_pending_for_repository(self, repository_id: str) -> bool:
        """这个知识库上是否还有文档变更在飞。

        调用方必须先跑一次启动恢复（``recover_document_mutations``）：那条路径会
        补偿掉崩溃遗留的意图并删行，剩下的才真的是"此刻正在进行"。否则一条陈旧的
        记录会让知识库永远删不掉，直到下次重启 —— 那不是保护，是卡死。
        """
        with self.database.session() as session:
            pending = session.scalar(
                select(func.count())
                .select_from(DocumentMutationRecord)
                .where(DocumentMutationRecord.repository_id == repository_id)
            )
        return bool(pending)

    def update(
        self,
        mutation_id: str,
        *,
        document_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> DocumentMutationRecord:
        with self.database.session() as session:
            record = session.get(DocumentMutationRecord, mutation_id)
            if record is None:
                raise DomainError("MUTATION_NOT_FOUND", "文档变更意图不存在", 409)
            if document_id is not None:
                record.document_id = document_id
            if payload is not None:
                record.payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            record.updated_at = utc_now()
            session.flush()
            return record

    def delete(self, mutation_id: str) -> None:
        with self.database.session() as session:
            record = session.get(DocumentMutationRecord, mutation_id)
            if record is not None:
                session.delete(record)


#: 导入时是否识别图片里的文字。
#:
#: 键与读法放在 `SettingStore` 上，因为**两个调用方都要它**：设置页读写它，导入服务在解析
#: 前读它落定进程开关。放在只有设置页能到的地方，导入服务就得反向依赖 API 层。
FIGURE_OCR_KEY = "import.figure_ocr"

#: 导入时是否用视觉模型描述图表语义。**默认关闭**。
#:
#: 打开它意味着把文档里的图片**发到模型服务商**。这与"本地优先"是相反的取舍，所以必须
#: 由用户显式打开，而不是默认启用 —— 一个默认开启的联网功能会让用户在不知情的情况下把
#: 资料发出去。只对"图里没有文字"的图发起：有文字的图 OCR 已经覆盖，再描述是白花钱。
FIGURE_DESCRIPTION_KEY = "import.figure_description"


class SettingStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def figure_ocr_enabled(self) -> bool:
        """导入时是否识别图片里的文字。**默认开** —— 关掉会让截图型课件失去内容。

        实测代价（53 页截图型课件）：关掉之后 sections 从 53 降到 36、正文从 23447 字符
        降到 15486，那 17 页没有文字层，识别是它们唯一的文字来源。
        """
        stored = self.get(FIGURE_OCR_KEY)
        if stored is None:
            return True
        return stored == "1"

    def set_figure_ocr(self, enabled: bool) -> None:
        self.set(FIGURE_OCR_KEY, "1" if enabled else "0")

    def figure_description_enabled(self) -> bool:
        """是否用视觉模型描述图表。**默认关闭**：打开会把图片发到模型服务商。"""
        return self.get(FIGURE_DESCRIPTION_KEY) == "1"

    def set_figure_description(self, enabled: bool) -> None:
        self.set(FIGURE_DESCRIPTION_KEY, "1" if enabled else "0")

    def get(self, key: str) -> str | None:
        with self.database.session() as session:
            record = session.get(SettingRecord, key)
            return record.value if record is not None else None

    def set(self, key: str, value: str) -> None:
        self.set_many({key: value})

    def set_many(self, values: dict[str, str]) -> None:
        with self.database.session() as session:
            for key, value in values.items():
                record = session.get(SettingRecord, key)
                if record is None:
                    session.add(SettingRecord(key=key, value=value))
                else:
                    record.value = value
                    record.updated_at = utc_now()


class RepositorySyncStateStore:
    """Durable last-known remote state and per-repository sync metadata."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def upsert(
        self,
        repository_id: str,
        document_id: str,
        title: str,
        content_sha256: str,
        url: str,
        path: Sequence[str] = (),
    ) -> None:
        with self.database.session() as session:
            record = session.get(RepositorySyncStateRecord, (repository_id, document_id))
            if record is None:
                session.add(
                    RepositorySyncStateRecord(
                        repository_id=repository_id,
                        document_id=document_id,
                        title=title,
                        content_sha256=content_sha256,
                        url=url,
                        path_json=json.dumps(list(path)),
                        last_seen_at=utc_now(),
                    )
                )
            else:
                record.title = title
                record.content_sha256 = content_sha256
                record.url = url
                record.path_json = json.dumps(list(path))
                record.last_seen_at = utc_now()
            session.flush()

    def get_snapshot(self, repository_id: str) -> dict[str, RemoteDocumentState]:
        with self.database.session() as session:
            records = session.scalars(
                select(RepositorySyncStateRecord).where(
                    RepositorySyncStateRecord.repository_id == repository_id
                )
            )
            return {
                record.document_id: RemoteDocumentState(
                    record.document_id,
                    record.title,
                    record.content_sha256,
                    record.url,
                    tuple(json.loads(record.path_json or "[]")),
                )
                for record in records
            }

    def discard(self, repository_id: str, document_id: str) -> None:
        """忘掉一篇文档的"上次见到的远端状态"。

        恢复一篇被移除的远端文档时必须调用它：同步用这份快照判重，快照还在且远端
        内容恰好没变过的话，恢复后那次同步会把它当 ``unchanged`` 跳过 —— 也就是说
        文档永远回不来。实测过：远端未变时不会重新导入，远端变了才会。

        与 ``mark_remote_deleted`` 的分工：那条路是"远端把它删了，本地留着标记"，
        这条是"我主动移除了，别再用旧快照挡住重新导入"。
        """
        with self.database.session() as session:
            record = session.get(RepositorySyncStateRecord, (repository_id, document_id))
            if record is not None:
                session.delete(record)

    def set_last_synced_at(self, repository_id: str, iso: str) -> None:
        value = datetime.fromisoformat(iso)
        with self.database.session() as session:
            record = session.get(RepositorySyncMetaRecord, repository_id)
            if record is None:
                session.add(
                    RepositorySyncMetaRecord(repository_id=repository_id, last_synced_at=value)
                )
            else:
                record.last_synced_at = value
            session.flush()

    def last_synced_at(self, repository_id: str) -> str | None:
        with self.database.session() as session:
            record = session.get(RepositorySyncMetaRecord, repository_id)
            if record is None or record.last_synced_at is None:
                return None
            return record.last_synced_at.isoformat()


class VersionStore:
    """Bounded per-document version snapshots."""

    def __init__(self, database: Database, *, max_versions: int = 20) -> None:
        self.database = database
        self.max_versions = max_versions

    def snapshot(self, document_id: str, title: str, content: str) -> DocumentVersion:
        with self.database.session() as session:
            current_max = session.scalar(
                select(func.max(DocumentVersionRecord.version_no)).where(
                    DocumentVersionRecord.document_id == document_id
                )
            )
            version_no = (current_max or 0) + 1
            record = DocumentVersionRecord(
                document_id=document_id,
                version_no=version_no,
                title=title,
                content=content,
                content_sha256=sha256(content.encode("utf-8")).hexdigest(),
                created_at=utc_now(),
            )
            session.add(record)
            session.flush()
            cutoff = version_no - self.max_versions
            if cutoff > 0:
                session.execute(
                    delete(DocumentVersionRecord).where(
                        DocumentVersionRecord.document_id == document_id,
                        DocumentVersionRecord.version_no <= cutoff,
                    )
                )
            return DocumentVersion(
                document_id=document_id,
                version_no=version_no,
                title=title,
                content=content,
                content_sha256=record.content_sha256,
                created_at=record.created_at.isoformat(),
            )

    def list(self, document_id: str) -> list[DocumentVersion]:
        with self.database.session() as session:
            records = session.scalars(
                select(DocumentVersionRecord)
                .where(DocumentVersionRecord.document_id == document_id)
                .order_by(DocumentVersionRecord.version_no)
            )
            return [
                DocumentVersion(
                    document_id=record.document_id,
                    version_no=record.version_no,
                    title=record.title,
                    content=record.content,
                    content_sha256=record.content_sha256,
                    created_at=record.created_at.isoformat(),
                )
                for record in records
            ]


class GraphStore:
    """Persists rule-extracted knowledge graph nodes and edges."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def replace_document_graph(
        self, document_id: str, nodes: list[GraphNode], edges: list[GraphEdge]
    ) -> None:
        with self.database.session() as session:
            session.execute(delete(GraphEdgeRecord).where(GraphEdgeRecord.source_id == document_id))
            session.execute(
                delete(GraphNodeRecord).where(GraphNodeRecord.document_id == document_id)
            )
            for node in nodes:
                session.add(
                    GraphNodeRecord(
                        id=node.id,
                        kind=node.kind,
                        label=node.label,
                        document_id=node.document_id,
                    )
                )
            for edge in edges:
                session.add(
                    GraphEdgeRecord(
                        source_id=edge.source_id,
                        target_id=edge.target_id,
                        relation=edge.relation,
                    )
                )
            session.flush()

    def list_nodes(self) -> list[GraphNode]:
        with self.database.session() as session:
            records = session.scalars(select(GraphNodeRecord).order_by(GraphNodeRecord.label))
            return [
                GraphNode(id=r.id, kind=r.kind, label=r.label, document_id=r.document_id)
                for r in records
            ]

    def adjacency(self, node_id: str) -> list[GraphEdge]:
        with self.database.session() as session:
            records = session.scalars(
                select(GraphEdgeRecord).where(
                    or_(
                        GraphEdgeRecord.source_id == node_id,
                        GraphEdgeRecord.target_id == node_id,
                    )
                )
            )
            return [
                GraphEdge(source_id=r.source_id, target_id=r.target_id, relation=r.relation)
                for r in records
            ]


class EmbeddingRebuildStore:
    """Marks documents whose vectors must be rebuilt after an embedding change."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def mark_needs_rebuild(self, document_id: str) -> None:
        with self.database.session() as session:
            record = session.get(EmbeddingRebuildRecord, document_id)
            if record is None:
                session.add(EmbeddingRebuildRecord(document_id=document_id, needs_rebuild=True))
            else:
                record.needs_rebuild = True
            session.flush()

    def clear(self, document_id: str) -> None:
        with self.database.session() as session:
            record = session.get(EmbeddingRebuildRecord, document_id)
            if record is not None:
                session.delete(record)

    def needs_rebuild(self, document_id: str) -> bool:
        with self.database.session() as session:
            record = session.get(EmbeddingRebuildRecord, document_id)
            return record is not None and record.needs_rebuild
