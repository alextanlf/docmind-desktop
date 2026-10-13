from __future__ import annotations

import json
import os
import time
from pathlib import Path
from uuid import uuid4

import pytest

from app.config import AppSettings
from app.imports.service import _encode_source_metadata
from app.imports.staging_cleanup import StagingReconciler
from app.storage.models import (
    BatchImportRecord,
    BatchSourceKind,
    BatchState,
    ImportJobRecord,
    ImportStatus,
)
from app.storage.repositories import BatchImportStore, ImportJobStore

#: 一个"就是没人引用"的时间点：mtime 往前挪 48 小时，宽限期不管是 24h 还是 0 都判得出差异。
_STALE_MTIME = time.time() - 48 * 3600


@pytest.fixture
def settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        session_token="staging-cleanup-token",
        data_dir=tmp_path / "data",
        environment="test",
        # 默认 0：绝大多数用例要的是"引用规则"，不是"时间规则"。要验时间规则的用例
        # 自己把它调回真实值，或者把 mtime 挪老。
        staging_orphan_grace_seconds=0.0,
    )


def reconciler(settings: AppSettings, database) -> StagingReconciler:
    return StagingReconciler(
        settings=settings,
        job_store=ImportJobStore(database),
        batch_store=BatchImportStore(database),
    )


def staged_file(settings: AppSettings, staged_id: str, payload: bytes = b"payload") -> Path:
    path = settings.staging_dir / f"{staged_id}.pdf"
    path.write_bytes(payload)
    return path


def add_job(database, staged_id: str, state: ImportStatus, *, collection_cache: dict | None = None) -> str:
    job_id = str(uuid4())
    source_value = _encode_source_metadata(
        value=staged_id,
        name="报告.pdf",
        fingerprint="a" * 64,
        duplicate_decision="create",
        job_id=job_id,
        collection_cache=collection_cache,
    )
    with database.session() as session:
        session.add(
            ImportJobRecord(
                id=job_id, source_kind="staged_file", source_value=source_value, state=state
            )
        )
    return job_id


def add_batch(
    database,
    state: BatchState,
    *,
    collection_id: str | None = None,
    source_kind: BatchSourceKind = BatchSourceKind.STAGED_DIRECTORY,
    batch_id: str | None = None,
) -> str:
    batch_id = batch_id or str(uuid4())
    descriptor = {"collectionId": collection_id} if collection_id else {}
    with database.session() as session:
        session.add(
            BatchImportRecord(
                id=batch_id,
                source_kind=source_kind,
                source_descriptor_json=json.dumps(descriptor),
                state=state,
            )
        )
    return batch_id


def age(path: Path) -> None:
    os.utime(path, (_STALE_MTIME, _STALE_MTIME))


# -- 单文件副本 ---------------------------------------------------------------


async def test_completed_job_staged_file_is_removed(database, settings: AppSettings) -> None:
    """导入走完之后，那份输入副本没有任何读取点了。

    这条用例因为「不再清理」而红 —— 那也是本机实测里残留 1.1 MB 的来源。
    """
    staged_id = str(uuid4())
    path = staged_file(settings, staged_id)
    add_job(database, staged_id, ImportStatus.COMPLETED)

    report = reconciler(settings, database).reconcile()

    assert not path.exists()
    assert (report.staged_files, report.bytes_reclaimed) == (1, len(b"payload"))


async def test_cancelled_job_staged_file_is_removed(database, settings: AppSettings) -> None:
    """取消也是终态：`reset_for_retry` 只接受 FAILED，不会再有人读它。"""
    staged_id = str(uuid4())
    path = staged_file(settings, staged_id)
    add_job(database, staged_id, ImportStatus.CANCELLED)

    reconciler(settings, database).reconcile()

    assert not path.exists()


@pytest.mark.parametrize(
    "state", [ImportStatus.PENDING, ImportStatus.PARSING, ImportStatus.UPLOADING, ImportStatus.INDEXING]
)
async def test_in_flight_job_staged_file_is_kept(
    database, settings: AppSettings, state: ImportStatus
) -> None:
    staged_id = str(uuid4())
    path = staged_file(settings, staged_id)
    add_job(database, staged_id, state)

    reconciler(settings, database).reconcile()

    assert path.exists()


async def test_failed_job_staged_file_survives_the_grace_window(
    database, tmp_path: Path
) -> None:
    """FAILED 必须留着 —— 它随时可能被重试，而且重试可能从 PARSING 这一档恢复。

    这条用例把宽限期设成 0 并且把文件 mtime 挪老：两条可删的理由都不成立，所以如果
    哪天有人把 FAILED 加进可删集合，它会立刻变红。
    """
    settings = AppSettings(
        session_token="t", data_dir=tmp_path / "data", environment="test",
        staging_orphan_grace_seconds=0.0,
    )
    staged_id = str(uuid4())
    path = staged_file(settings, staged_id)
    age(path)
    add_job(database, staged_id, ImportStatus.FAILED)

    report = reconciler(settings, database).reconcile()

    assert path.exists()
    assert report.staged_files == 0


async def test_batch_child_job_does_not_claim_a_loose_file(database, settings: AppSettings) -> None:
    """批次子任务的 value 是缓存 id，不是暂存根目录下的文件名。

    如果对账把它当成"这份文件还有人要"，一条陈旧的批次子任务就能让同名文件永远删不掉。
    """
    staged_id = str(uuid4())
    path = staged_file(settings, staged_id)
    age(path)
    add_job(
        database,
        staged_id,
        ImportStatus.FAILED,
        collection_cache={"collection_id": str(uuid4()), "cache_ref": {}, "title": "t"},
    )

    reconciler(settings, database).reconcile()

    assert not path.exists()


async def test_unreferenced_orphan_needs_to_age_out(database, tmp_path: Path) -> None:
    """没有任务引用的副本 = 崩溃残留，或者"用户选好了文件还没点确认"。

    两者在数据库里长得一样，所以只能靠时间区分：宽限期内不碰，过了才删。用真实宽限期
    （24 小时）而不是 0，否则测的就成了"是否删除"而不是"是否按时间删除"。
    """
    settings = AppSettings(
        session_token="t", data_dir=tmp_path / "data", environment="test",
        staging_orphan_grace_seconds=24 * 3600.0,
    )
    fresh_id, stale_id = str(uuid4()), str(uuid4())
    fresh, stale = staged_file(settings, fresh_id), staged_file(settings, stale_id)
    age(stale)

    report = reconciler(settings, database).reconcile()

    assert fresh.exists()  # 刚放进来：可能用户正开着导入对话框
    assert not stale.exists()
    assert report.staged_files == 1


async def test_live_partial_copy_is_protected_by_the_grace_window(
    database, tmp_path: Path
) -> None:
    """`.partial` 是正在拷贝的副本（主进程先写它再 rename）。

    它天然没有任务引用，所以只有宽限期拦得住"扫描把自己正在写的东西删了"。
    """
    settings = AppSettings(
        session_token="t", data_dir=tmp_path / "data", environment="test",
        staging_orphan_grace_seconds=24 * 3600.0,
    )
    partial = settings.staging_dir / f"{uuid4()}.pdf.partial"
    partial.write_bytes(b"half a file")

    reconciler(settings, database).reconcile()

    assert partial.exists()


async def test_unrelated_files_in_staging_are_never_touched(database, tmp_path: Path) -> None:
    """认不出来的东西一律不碰 —— 清扫的删除集是白名单，不是"除了 X 都删"。"""
    settings = AppSettings(
        session_token="t", data_dir=tmp_path / "data", environment="test",
        staging_orphan_grace_seconds=0.0,
    )
    notes = settings.staging_dir / "notes.txt"
    notes.write_text("别删我", encoding="utf-8")
    age(notes)

    report = reconciler(settings, database).reconcile()

    assert notes.exists()
    assert report.staged_files == 0


# -- 目录集合与远程缓存 -------------------------------------------------------


@pytest.mark.parametrize(
    "state", [BatchState.COMPLETED, BatchState.FAILED, BatchState.CANCELLED]
)
async def test_finished_batch_collection_is_removed(
    database, settings: AppSettings, state: BatchState
) -> None:
    collection_id = str(uuid4())
    root = settings.staging_dir / "collections" / collection_id
    (root / "items").mkdir(parents=True)
    (root / "items" / "a").write_bytes(b"x" * 10)
    (root / "manifest.json").write_text("{}", encoding="utf-8")
    add_batch(database, state, collection_id=collection_id)

    report = reconciler(settings, database).reconcile()

    assert not root.exists()
    assert (report.collections, report.bytes_reclaimed) == (1, 12)


@pytest.mark.parametrize(
    "state",
    [
        BatchState.DISCOVERING,
        BatchState.AWAITING_CONFIRMATION,
        BatchState.RUNNING,
        BatchState.PAUSED,
        # 关键一条：它还能回到 RUNNING 重跑失败的条目（retry_items 只拒绝三个终态），
        # 所以它的 items 必须原样留着。
        BatchState.COMPLETED_WITH_ERRORS,
    ],
)
async def test_unfinished_batch_collection_is_kept(
    database, tmp_path: Path, state: BatchState
) -> None:
    settings = AppSettings(
        session_token="t", data_dir=tmp_path / "data", environment="test",
        # 宽限期 0：留着它的理由必须只能是"批次还没完"，不能是"还年轻"。
        staging_orphan_grace_seconds=0.0,
    )
    collection_id = str(uuid4())
    root = settings.staging_dir / "collections" / collection_id
    (root / "items").mkdir(parents=True)
    (root / "items" / "a").write_bytes(b"x")
    age(root)
    add_batch(database, state, collection_id=collection_id)

    report = reconciler(settings, database).reconcile()

    assert root.exists()
    assert report.collections == 0


async def test_batch_retry_keeps_the_collection_alive(database, tmp_path: Path) -> None:
    """批次回到 RUNNING 之后，集合目录必须还在（它是重跑唯一的内容来源）。"""
    settings = AppSettings(
        session_token="t", data_dir=tmp_path / "data", environment="test",
        staging_orphan_grace_seconds=0.0,
    )
    collection_id = str(uuid4())
    root = settings.staging_dir / "collections" / collection_id
    (root / "items").mkdir(parents=True)
    (root / "items" / "a").write_bytes(b"x")
    add_batch(database, BatchState.RUNNING, collection_id=collection_id)

    reconciler(settings, database).reconcile()

    assert (root / "items" / "a").exists()


async def test_remote_cache_follows_its_batch(database, tmp_path: Path) -> None:
    """网页/远程批次的内容缓存在 `remote/<batchId>/`，标识就是批次 id 本身。"""
    settings = AppSettings(
        session_token="t", data_dir=tmp_path / "data", environment="test",
        staging_orphan_grace_seconds=0.0,
    )
    done = add_batch(
        database, BatchState.COMPLETED, source_kind=BatchSourceKind.WEB
    )
    running = add_batch(database, BatchState.RUNNING, source_kind=BatchSourceKind.WEB)
    for batch_id in (done, running):
        cache = settings.staging_dir / "remote" / batch_id
        cache.mkdir(parents=True)
        (cache / "payload").write_bytes(b"y" * 4)

    report = reconciler(settings, database).reconcile()

    assert not (settings.staging_dir / "remote" / done).exists()
    assert (settings.staging_dir / "remote" / running / "payload").exists()
    assert (report.remote_caches, report.bytes_reclaimed) == (1, 4)


async def test_unreferenced_collection_needs_to_age_out(database, tmp_path: Path) -> None:
    """确认到一半崩掉的集合目录：没有批次引用，靠时间回收。"""
    settings = AppSettings(
        session_token="t", data_dir=tmp_path / "data", environment="test",
        staging_orphan_grace_seconds=24 * 3600.0,
    )
    fresh = settings.staging_dir / "collections" / str(uuid4())
    stale = settings.staging_dir / "collections" / str(uuid4())
    for root in (fresh, stale):
        (root / "items").mkdir(parents=True)
        (root / "items" / "a").write_bytes(b"x")
    age(stale)

    report = reconciler(settings, database).reconcile()

    assert fresh.exists()
    assert not stale.exists()
    assert report.collections == 1


async def test_bad_batch_descriptor_is_reported_and_left_to_the_clock(
    database, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """描述符坏掉时说不出它用哪个集合：跳过、不猜，回收交给时间宽限。

    这里断言的是**两件事**：报告里能看见跳过了几个批次（否则现场无法判断"为什么没删"），
    以及刚刚放进来的集合不会被一次误判带走。
    """
    settings = AppSettings(
        session_token="t", data_dir=tmp_path / "data", environment="test",
        staging_orphan_grace_seconds=24 * 3600.0,
    )
    with database.session() as session:
        session.add(
            BatchImportRecord(
                id=str(uuid4()),
                source_kind=BatchSourceKind.STAGED_DIRECTORY,
                source_descriptor_json="not json at all",
                state=BatchState.COMPLETED,
            )
        )
    root = settings.staging_dir / "collections" / str(uuid4())
    (root / "items").mkdir(parents=True)

    with caplog.at_level("WARNING"):
        report = reconciler(settings, database).reconcile()

    assert root.exists()
    assert report.skipped_batches == 1
    # 现场要能自己判断"为什么没删"：跳过的批次必须留下日志，而不是只体现在返回值里。
    assert "跳过 1 个描述符无法解析的批次" in caplog.text


# -- 安全 --------------------------------------------------------------------


async def test_symlinked_directory_inside_collections_is_not_followed(
    database, settings: AppSettings, tmp_path: Path
) -> None:
    """暂存目录里放一个指向别处的符号链接，清扫不能删到目录树外面去。"""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("保命文件", encoding="utf-8")
    link = settings.staging_dir / "collections" / str(uuid4())
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside, target_is_directory=True)

    report = reconciler(settings, database).reconcile()

    assert (outside / "keep.txt").exists()
    assert report.collections == 0


async def test_missing_staging_directory_is_not_an_error(tmp_path: Path, database) -> None:
    settings = AppSettings(
        session_token="t", data_dir=tmp_path / "data", environment="test"
    )
    # 明确删掉：全新安装与"用户手动清过数据目录"都会碰到这个形状。
    for entry in settings.staging_dir.iterdir():
        entry.unlink()
    settings.staging_dir.rmdir()

    report = reconciler(settings, database).reconcile()

    assert report.removed == 0


def _partial_scan(settings: AppSettings, manifest: str | None = "{}") -> Path:
    """被中断的扫描留下的半成品：`collections/<id>.partial/`。"""
    root = settings.staging_dir / "collections" / f"{uuid4()}.partial"
    (root / "items").mkdir(parents=True, exist_ok=True)
    if manifest is not None:
        (root / "manifest.json").write_text(manifest, encoding="utf-8")
    return root


async def test_a_half_written_scan_is_removed_without_waiting(
    database, tmp_path: Path
) -> None:
    """`<id>.partial` 立刻清掉，**不等**宽限期。

    理由很硬：扫描只活在进程内，而对账清扫跑在启动时 —— 那时不可能有扫描在跑，
    所以看到的每一个 `.partial` 按定义都是孤儿。普通集合目录不同：它可能是别的进程
    正在用的，所以那边必须看引用与时间。

    这条用例用**真实宽限期**（24 小时）而且半成品是**刚写出来的**：如果哪天有人把
    `.partial` 也丢进宽限期分支，它会立刻变红。
    """
    settings = AppSettings(
        session_token="t", data_dir=tmp_path / "data", environment="test",
        staging_orphan_grace_seconds=24 * 3600.0,
    )
    partial = _partial_scan(settings)

    report = reconciler(settings, database).reconcile()

    assert not partial.exists()
    assert report.partial_scans == 1
    assert report.bytes_reclaimed > 0
    # 它算进"删掉了东西"，否则日志与统计会漏掉这一类。
    assert report.removed == 1


async def test_a_partial_scan_is_removed_even_if_a_batch_names_it(
    database, tmp_path: Path
) -> None:
    """就算有批次"引用"这个名字也照删。

    `.partial` 是"尚未发布"的标记：批次只可能引用发布后的集合 id（那份清单才是它读的东西）。
    真出现这种引用，说明描述符是硬造的，而这时删掉半成品仍然是对的 ——
    留着它只会让"这份清单到底可用不可用"变得说不清。
    """
    settings = AppSettings(
        session_token="t", data_dir=tmp_path / "data", environment="test",
        staging_orphan_grace_seconds=24 * 3600.0,
    )
    partial = _partial_scan(settings)
    add_batch(database, BatchState.RUNNING, collection_id=partial.name)

    report = reconciler(settings, database).reconcile()

    assert not partial.exists()
    assert report.partial_scans == 1
    assert report.collections == 0
