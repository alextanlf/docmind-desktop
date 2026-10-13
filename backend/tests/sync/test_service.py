from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.schemas.sync import RemoteDocumentState
from app.sync.service import IncrementalSyncService


class FakeDoc:
    def __init__(self, remote_id: str | None, markdown_path: str | None = "") -> None:
        self.remote_id = remote_id
        self.markdown_path = markdown_path


class FakeDocumentStore:
    def __init__(self, docs: list[FakeDoc]) -> None:
        self.docs = docs

    def list_for_repository(self, repository_id: str) -> list[FakeDoc]:
        return self.docs


class FakeRefresher:
    def __init__(self) -> None:
        self.upserts: list[tuple[str, str, str]] = []
        # 单独记路径：既有的断言比的是三元组，不该为了这次扩展去改它们。
        self.paths: list[tuple[str, tuple[str, ...]]] = []
        self.deleted: list[tuple[str, str]] = []
        self.fail_on: set[str] = set()

    async def upsert_from_remote(
        self,
        repository_id: str,
        document_id: str,
        title: str,
        content: str,
        path: Sequence[str] = (),
    ) -> str:
        if document_id in self.fail_on:
            raise RuntimeError("boom")
        self.upserts.append((repository_id, document_id, title))
        self.paths.append((document_id, tuple(path)))
        return "local-id"

    async def mark_remote_deleted(self, repository_id: str, document_id: str) -> None:
        self.deleted.append((repository_id, document_id))


class FakeSyncStateStore:
    def __init__(self, snapshot: dict[str, RemoteDocumentState] | None = None) -> None:
        self.snapshot = dict(snapshot or {})
        self.upserts: list[tuple[str, str]] = []
        self.last_synced: str | None = None

    def get_snapshot(self, repository_id: str) -> dict[str, RemoteDocumentState]:
        return dict(self.snapshot)

    def upsert(
        self,
        repository_id: str,
        document_id: str,
        title: str,
        content_sha256: str,
        url: str,
        path: Sequence[str] = (),
    ) -> None:
        del path
        self.upserts.append((repository_id, document_id))

    def set_last_synced_at(self, repository_id: str, iso: str) -> None:
        self.last_synced = iso


class FakeRemovedDocumentStore:
    """用户主动移除的远端文档，只实现同步循环会用到的那一个方法。"""

    def __init__(self, removed: set[str]) -> None:
        self.removed = removed

    def identities(self, repository_id: str) -> set[str]:
        return set(self.removed)


def _service(
    *,
    store: FakeSyncStateStore,
    docs: list[FakeDoc],
    remote: list[tuple[RemoteDocumentState, str]],
    refresher: FakeRefresher,
    removed: set[str] | None = None,
) -> IncrementalSyncService:
    async def reader(repository_id: str) -> list[tuple[RemoteDocumentState, str]]:
        return remote

    return IncrementalSyncService(
        sync_state_store=store,
        snapshot_reader=reader,
        refresher=refresher,
        document_store=FakeDocumentStore(docs),
        removed_document_store=(
            FakeRemovedDocumentStore(removed) if removed is not None else None
        ),
    )


@pytest.mark.asyncio
async def test_sync_reconciles_bound_documents_only() -> None:
    store = FakeSyncStateStore(
        {
            "doc-A": RemoteDocumentState("doc-A", "A", "hash-A", ""),
            "doc-B": RemoteDocumentState("doc-B", "B1", "hash-B1", ""),
            "doc-D": RemoteDocumentState("doc-D", "D", "hash-D", ""),
        }
    )
    docs = [FakeDoc("doc-A"), FakeDoc("doc-B"), FakeDoc("doc-C"), FakeDoc("doc-D")]
    remote = [
        (RemoteDocumentState("doc-A", "A", "hash-A", ""), "# A"),
        (RemoteDocumentState("doc-B", "B2", "hash-B2", ""), "# B2"),
        (RemoteDocumentState("doc-C", "C", "hash-C", ""), "# C"),
        (RemoteDocumentState("doc-E", "E", "hash-E", ""), "# E"),
    ]
    refresher = FakeRefresher()

    outcome = await _service(
        store=store, docs=docs, remote=remote, refresher=refresher
    ).sync_repository("repo-1")

    assert (outcome.added, outcome.changed, outcome.deleted, outcome.unchanged, outcome.failed) == (
        2, 1, 1, 1, 0,
    )
    assert refresher.upserts == [
        ("repo-1", "doc-B", "B2"),
        ("repo-1", "doc-C", "C"),
        ("repo-1", "doc-E", "E"),
    ]
    assert refresher.deleted == [("repo-1", "doc-D")]


@pytest.mark.asyncio
async def test_sync_continues_after_document_failure() -> None:
    store = FakeSyncStateStore(
        {"doc-A": RemoteDocumentState("doc-A", "A", "hash-A", "")}
    )
    docs = [FakeDoc("doc-A")]
    remote = [(RemoteDocumentState("doc-A", "A2", "hash-A2", ""), "# A2")]
    refresher = FakeRefresher()
    refresher.fail_on.add("doc-A")

    outcome = await _service(
        store=store, docs=docs, remote=remote, refresher=refresher
    ).sync_repository("repo-1")

    assert (outcome.added, outcome.changed, outcome.deleted, outcome.unchanged, outcome.failed) == (
        0, 0, 0, 0, 1,
    )


@pytest.mark.asyncio
async def test_sync_repairs_existing_remote_document_without_markdown_file() -> None:
    store = FakeSyncStateStore(
        {"doc-A": RemoteDocumentState("doc-A", "A", "hash-A", "")}
    )
    docs = [FakeDoc("doc-A", markdown_path=None)]
    remote = [(RemoteDocumentState("doc-A", "A", "hash-A", ""), "# A")]
    refresher = FakeRefresher()

    outcome = await _service(
        store=store, docs=docs, remote=remote, refresher=refresher
    ).sync_repository("repo-1")

    assert outcome.unchanged == 1
    assert refresher.upserts == [("repo-1", "doc-A", "A")]


@pytest.mark.asyncio
async def test_sync_never_resurrects_a_removed_document_when_the_remote_changed() -> None:
    """用户移除了的文档，哪怕远端改了也必须继续被跳过。

    这就是"移除"能不能成立的分水岭：判重看的是 `repository_sync_state`（上次见到
    的远端状态），而不是本地还有没有这篇文档。一篇还在更新的远端文档，只要远端
    内容变了就会被重新导入 —— 对一个不断在变的语雀知识库来说，"移除"会被一次次
    撤销，用户处理过的东西会自己回来。
    """
    store = FakeSyncStateStore({"doc-1": RemoteDocumentState("doc-1", "A", "hash-1", "")})
    refresher = FakeRefresher()

    outcome = await _service(
        store=store,
        docs=[],
        remote=[(RemoteDocumentState("doc-1", "A2", "hash-2", ""), "# A2")],
        refresher=refresher,
        removed={"doc-1"},
    ).sync_repository("repo-1")

    assert refresher.upserts == []
    assert (outcome.added, outcome.changed) == (0, 0)


@pytest.mark.asyncio
async def test_sync_still_imports_everything_that_was_not_removed() -> None:
    """过滤必须精确到 id：漏一篇（或多跳一篇）都会让用户丢掉没处理过的内容。"""
    store = FakeSyncStateStore(
        {
            "doc-keep": RemoteDocumentState("doc-keep", "Keep", "hash-keep", ""),
            "doc-drop": RemoteDocumentState("doc-drop", "Drop", "hash-drop", ""),
        }
    )
    refresher = FakeRefresher()

    outcome = await _service(
        store=store,
        docs=[],
        remote=[
            # 未变、已变、以及一篇全新的：都没被移除，就该照常处理。
            (RemoteDocumentState("doc-keep", "Keep", "hash-keep", ""), "# Keep"),
            (RemoteDocumentState("doc-drop", "Drop changed", "hash-drop-2", ""), "# Drop"),
            (RemoteDocumentState("doc-new", "New", "hash-new", ""), "# New"),
        ],
        refresher=refresher,
        removed={"doc-drop"},
    ).sync_repository("repo-1")

    assert refresher.upserts == [("repo-1", "doc-new", "New")]
    assert (outcome.added, outcome.changed, outcome.unchanged) == (1, 0, 1)


@pytest.mark.asyncio
async def test_sync_reacts_to_a_directory_change_alone() -> None:
    """正文没变、只是被挪了目录，也必须算变更并把新路径传下去。

    否则界面上的树永远停在上一次的位置：用户在一个目录里"清理过了"，那篇文档
    其实已经被挪到别处，按目录移除时就会漏掉它。
    """
    store = FakeSyncStateStore(
        {"doc-1": RemoteDocumentState("doc-1", "A", "hash-1", "", ("旧目录",))}
    )
    refresher = FakeRefresher()

    outcome = await _service(
        store=store,
        docs=[FakeDoc("doc-1")],
        remote=[
            (RemoteDocumentState("doc-1", "A", "hash-1", "", ("新目录",)), "# A")
        ],
        refresher=refresher,
    ).sync_repository("repo-1")

    assert outcome.changed == 1
    assert refresher.paths == [("doc-1", ("新目录",))]


@pytest.mark.asyncio
async def test_sync_leaves_an_unchanged_directory_alone() -> None:
    """路径没变就不该重新导入 —— 否则每次同步都全量重嵌入一遍。"""
    store = FakeSyncStateStore(
        {"doc-1": RemoteDocumentState("doc-1", "A", "hash-1", "", ("同一目录",))}
    )
    refresher = FakeRefresher()

    outcome = await _service(
        store=store,
        docs=[FakeDoc("doc-1")],
        remote=[
            (RemoteDocumentState("doc-1", "A", "hash-1", "", ("同一目录",)), "# A")
        ],
        refresher=refresher,
    ).sync_repository("repo-1")

    assert (outcome.changed, outcome.unchanged) == (0, 1)
    assert refresher.upserts == []
