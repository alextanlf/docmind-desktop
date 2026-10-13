"""清扫 ``imports/staging`` 里已经没人要的副本。

暂存副本是导入的**输入**，不是产物：用户选中的文件会先被复制进 ``imports/staging``
（单文件是 ``<uuid>.<ext>``，目录批次是 ``collections/<id>/``，网页/远程批次是
``remote/<batchId>/``），解析落盘之后就没有任何读取点了 —— 唯一读原件的是 PDF 原件
视图，读的是 ``documents/<id>/raw.<后缀>``（见 ``imports/original_copies.py``）。
不清理的话它会一直躺在那里：实测某台机器上残留比正式存储还大 2.8 倍
（见 docs/文档存储优化方案.md）。

## 为什么是"对账"而不是"在结束时顺手删"

删除时机分散在三个写入者身上 —— 单文件由 electron 主进程写、集合由目录扫描写、远程缓存
由后端自己写；而"还会不会再读"这件事只在数据库里（任务状态、批次状态）。所以这里不挂
在某个流程的出口上，而是**拿数据库对账**：谁处于不可能再读的状态，就删谁。

它对**中断**天然免疫：应用崩在导入中途，磁盘上多出一份没人引用的副本，下次启动照样收掉；
而它也不会误删 —— 判定依据只有"引用它的任务/批次是否已到终态"，外加一条时间宽限。

## 为什么保留 FAILED

任务 FAILED 只是**暂时**终态：``ImportJobStore.reset_for_retry`` 和 ``retry_items`` 都
允许重跑，而且重跑可能从 ``PARSING`` 这一档恢复（应用重启导致的中断就是这样），那时
它还要重新读暂存文件。批次的 ``COMPLETED_WITH_ERRORS`` 同理 —— 它的状态机明确能回到
``RUNNING``。所以可删的终态是**窄**的：任务只有 COMPLETED / CANCELLED，批次只有
COMPLETED / FAILED / CANCELLED。
"""

from __future__ import annotations

import logging
import os
import shutil
import stat as stat_module
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from app.api.errors import DomainError
from app.config import AppSettings
from app.imports.service import collection_id_for_batch
from app.storage.models import BatchState, ImportStatus
from app.storage.repositories import (
    BatchImportStore,
    ImportJobStore,
    _normalized_source_metadata,
)

logger = logging.getLogger(__name__)

#: 任务到了这些状态就不会再读暂存文件：走完了，或者用户取消了（取消后不可重试）。
_DISCARDABLE_JOB_STATES = frozenset({ImportStatus.COMPLETED, ImportStatus.CANCELLED})

#: 批次到了这些状态就不会再读集合缓存。``COMPLETED_WITH_ERRORS`` **不在**其中：
#: ``BatchService.retry_items`` 会把它移回 RUNNING 并重跑失败的条目。
_DISCARDABLE_BATCH_STATES = frozenset(
    {BatchState.COMPLETED, BatchState.FAILED, BatchState.CANCELLED}
)


@dataclass(frozen=True)
class StagingCleanupReport:
    """一次清扫删掉了什么。数字分开记，是为了让日志/测试能区分三类残留。"""

    staged_files: int = 0
    collections: int = 0
    remote_caches: int = 0
    #: 被中断的目录扫描留下的半成品（`<collectionId>.partial`）。单独记，是因为它的
    #: 清理规则与其它残留不同：**不等宽限期**，见 `_sweep_collections`。
    partial_scans: int = 0
    bytes_reclaimed: int = 0
    #: 描述符解析不了、因而**跳过**的批次数。宁可漏删也不能错删，所以这一项是可见的。
    skipped_batches: int = 0

    @property
    def removed(self) -> int:
        return self.staged_files + self.collections + self.remote_caches + self.partial_scans


@dataclass(frozen=True)
class _References:
    """谁还在引用暂存副本。

    值都是**集合**而不是单个状态：同一个 staged id 理论上能被多个任务引用（同一份文件
    连导两次），只要有一个还在跑就必须留；一个集合目录同理。
    """

    files: dict[str, set[ImportStatus]]
    collections: dict[str, set[BatchState]]
    batches: dict[str, BatchState]
    skipped_batches: int


class StagingReconciler:
    def __init__(
        self,
        *,
        settings: AppSettings,
        job_store: ImportJobStore,
        batch_store: BatchImportStore,
    ) -> None:
        self.staging_dir = settings.staging_dir
        self.grace_seconds = settings.staging_orphan_grace_seconds
        self.job_store = job_store
        self.batch_store = batch_store

    def reconcile(self) -> StagingCleanupReport:
        if not self.staging_dir.is_dir():
            return StagingCleanupReport()
        references = self._references()
        staged_files = collections = remote_caches = partial_scans = reclaimed = 0

        for path in sorted(self.staging_dir.iterdir()):
            if path.name == "collections":
                count, freed, partials = self._sweep_collections(path, references.collections)
                collections += count
                partial_scans += partials
                reclaimed += freed
            elif path.name == "remote":
                count, freed = self._sweep_remote(path, references.batches)
                remote_caches += count
                reclaimed += freed
            elif path.is_file() and self._loose_file_is_reclaimable(path, references.files):
                reclaimed += _remove_file(path)
                staged_files += 1

        report = StagingCleanupReport(
            staged_files=staged_files,
            collections=collections,
            remote_caches=remote_caches,
            partial_scans=partial_scans,
            bytes_reclaimed=reclaimed,
            skipped_batches=references.skipped_batches,
        )
        # 删了东西就必须留下痕迹：用户发现"导入时选的那个文件不见了"时，日志是唯一的解释。
        # 报告对象本身也返回给调用方，留给测试与将来的周期触发。
        if report.removed:
            logger.info(
                "暂存清扫：删除单文件副本 %d 个、集合 %d 个、远程缓存 %d 个、"
                "被中断扫描的半成品 %d 个，回收 %d 字节",
                report.staged_files,
                report.collections,
                report.remote_caches,
                report.partial_scans,
                report.bytes_reclaimed,
            )
        if report.skipped_batches:
            logger.warning(
                "暂存清扫：跳过 %d 个描述符无法解析的批次，它们的集合只能等宽限期回收",
                report.skipped_batches,
            )
        return report

    # -- 谁还引用着什么 ----------------------------------------------------

    def _references(self) -> _References:
        """把数据库里"还在引用暂存副本"的引用方收齐。"""
        files: dict[str, set[ImportStatus]] = {}
        for job in self.job_store.list():
            if job.source_kind != "staged_file":
                continue
            metadata = _normalized_source_metadata(job)
            # 批次子任务也是 staged_file，但它的字节在 collections/ 或 remote/ 下，
            # 由批次的生命周期管；这里认了反而会把它当成"没有对应文件"而漏删记录。
            if isinstance(metadata.get("collection_cache"), dict):
                continue
            value = metadata.get("value")
            if isinstance(value, str) and value:
                files.setdefault(value.lower(), set()).add(job.state)

        collections: dict[str, set[BatchState]] = {}
        batches: dict[str, BatchState] = {}
        skipped = 0
        for batch in self.batch_store.list_batches():
            batches[str(batch.id)] = batch.state
            try:
                collection_id = collection_id_for_batch(batch)
            except DomainError:
                # 描述符坏了就说不清它用哪个集合：跳过，让时间宽限去兜底。
                skipped += 1
                continue
            collections.setdefault(collection_id, set()).add(batch.state)
        return _References(
            files=files, collections=collections, batches=batches, skipped_batches=skipped
        )

    # -- 三类残留 ----------------------------------------------------------

    def _loose_file_is_reclaimable(
        self, path: Path, referenced_files: dict[str, set[ImportStatus]]
    ) -> bool:
        """单文件副本该不该删。

        有引用方：只有全部引用方都到了可删终态才删（FAILED 与在跑的一律留）。
        没有引用方：这是崩溃残留，或者用户选了文件还没确认 —— 只有超过宽限期才删，
        因为"刚放进来还没建任务"和"放了两天的垃圾"在数据库里长得一模一样。
        """
        staged_id = path.name.split(".", 1)[0].lower()
        try:
            UUID(staged_id)
        except ValueError:
            # 不是暂存副本（比如将来放进来的旁挂文件）：不碰。
            return False
        states = referenced_files.get(staged_id)
        if states is None:
            return self._older_than_grace(path)
        return all(state in _DISCARDABLE_JOB_STATES for state in states)

    def _sweep_collections(
        self, root: Path, referenced: dict[str, set[BatchState]]
    ) -> tuple[int, int, int]:
        """清掉目录批次残留。返回（删掉的集合数、删掉的半成品数、回收字节）。"""
        removed = freed = partials = 0
        for path in sorted(root.iterdir()):
            if not _is_real_directory(path):
                continue
            if _is_partial_scan(path.name):
                # **半成品不等宽限期。** 扫描只在进程内存在，`<id>.partial` 这个后缀
                # 是"还没发布"的标记；对账清扫跑在启动时，那时不可能有扫描在跑，
                # 所以看到的每一个 .partial 按定义都是孤儿 —— 等 24 小时只是让垃圾多躺一天。
                # （对比：普通集合目录可能是别的进程正在用的，所以那边必须看引用与时间。）
                freed += _remove_tree(path)
                partials += 1
                continue
            states = referenced.get(path.name)
            if states is None:
                if not self._older_than_grace(path):
                    continue
            elif not all(state in _DISCARDABLE_BATCH_STATES for state in states):
                continue
            freed += _remove_tree(path)
            removed += 1
        return removed, freed, partials

    def _sweep_remote(self, root: Path, batches: dict[str, BatchState]) -> tuple[int, int]:
        removed = freed = 0
        for path in sorted(root.iterdir()):
            if not _is_real_directory(path):
                continue
            state = batches.get(path.name)
            if state is None:
                if not self._older_than_grace(path):
                    continue
            elif state not in _DISCARDABLE_BATCH_STATES:
                continue
            freed += _remove_tree(path)
            removed += 1
        return removed, freed

    def _older_than_grace(self, path: Path) -> bool:
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            # 刚被别的进程删掉/拿不到时间戳：当作"不老"，这一次不删。
            return False
        return age >= self.grace_seconds


def _is_partial_scan(name: str) -> bool:
    """`<collectionId>.partial` —— 目录扫描写清单期间使用的名字，发布时会被 rename 掉。

    只认这个后缀，不认"含 .partial 的所有东西"：清单与来源注册表自己也会用
    `xxx.<uuid>.partial` 这种临时文件，那些在目录**内部**，随目录一起被删。
    """
    return name.endswith(".partial")


def _is_real_directory(path: Path) -> bool:
    """是目录、而且不是符号链接。

    用 ``lstat``：``is_dir()`` 会跟着符号链接走，于是有人在暂存目录里放一个指向家目录的
    链接，清扫就会删到目录树外面去。
    """
    try:
        return stat_module.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


def _directory_size(path: Path) -> int:
    total = 0
    for entry in path.rglob("*"):
        try:
            info = os.lstat(entry)
        except OSError:
            continue
        if stat_module.S_ISREG(info.st_mode):
            total += info.st_size
    return total


def _remove_file(path: Path) -> int:
    try:
        info = os.lstat(path)
    except OSError:
        return 0
    if not stat_module.S_ISREG(info.st_mode):
        return 0
    try:
        path.unlink()
    except OSError:
        return 0
    return info.st_size


def _remove_tree(path: Path) -> int:
    size = _directory_size(path)
    try:
        shutil.rmtree(path)
    except OSError:
        return 0
    return size
