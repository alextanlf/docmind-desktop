from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from uuid import UUID

from app.api.errors import DomainError
from app.document.discovery import DirectoryDiscovery
from app.imports.events import EventType, ImportEventBroker
from app.schemas.batches import (
    BatchItemPage,
    CreateBatchRequest,
    DiscoveryRequest,
    StagedDirectoryBatchRequest,
)
from app.storage.models import BatchImportRecord, BatchItemState, BatchState
from app.storage.repositories import BatchImportStore

MAX_BATCH_CONCURRENCY = 3


class BatchService:
    """Durable coordinator for discovery decisions and child import jobs."""

    def __init__(
        self,
        *,
        store: BatchImportStore,
        import_service: Any,
        document_store: Any | None = None,
        event_broker: ImportEventBroker | None = None,
        max_concurrency: int = MAX_BATCH_CONCURRENCY,
        staging_root: Path | None = None,
        manifest_max_bytes: int = 2 * 1024 * 1024,
        batch_max_items: int = 1000,
    ) -> None:
        if max_concurrency != MAX_BATCH_CONCURRENCY:
            raise ValueError("batch concurrency is fixed at three")
        self.store = store
        self.import_service = import_service
        self.document_store = document_store
        self.event_broker = event_broker
        self.staging_root = Path(staging_root).resolve() if staging_root is not None else None
        self.manifest_max_bytes = manifest_max_bytes
        self.batch_max_items = batch_max_items
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._batch_locks: dict[str, asyncio.Lock] = {}
        self._batch_tasks: dict[str, dict[str, asyncio.Task[None]]] = {}
        self._cancel_tasks: dict[str, asyncio.Task[Any]] = {}
        self._cancel_context: ContextVar[frozenset[str]] = ContextVar(
            f"batch_cancel_context_{id(self)}", default=frozenset()
        )

    def get(self, batch_id: str):
        batch = self.store.get(batch_id)
        if batch is None:
            raise DomainError("BATCH_NOT_FOUND", "批次不存在", 404)
        return batch

    def list_batches(self) -> list[Any]:
        return self.store.list_batches()

    def list_items(self, batch_id: str, **kwargs: Any) -> BatchItemPage:
        self.get(batch_id)
        try:
            return self.store.list_items(batch_id, **kwargs)
        except ValueError:
            raise DomainError("INVALID_REQUEST", "分页参数无效", 422) from None

    def create_batch(self, body: CreateBatchRequest) -> BatchImportRecord:
        if not isinstance(body, StagedDirectoryBatchRequest):
            raise DomainError("INVALID_REQUEST", "当前仅支持 staged_directory 来源", 422)
        batch = BatchImportRecord(
            source_kind=body.kind,
            source_descriptor_json=json.dumps({"collectionId": str(body.source_id)}, separators=(",", ":")),
            repository_id=str(body.repository_id),
            state=BatchState.DISCOVERING,
            message="正在发现目录",
        )
        return self.store.create_batch(batch)

    async def discover_batch(self, batch_id: str) -> None:
        batch = self.get(batch_id)
        if self.staging_root is None:
            self.store.fail_discovery(batch_id, code="IMPORT_FAILED", message="目录发现失败", retryable=False)
            return
        try:
            descriptor = json.loads(batch.source_descriptor_json)
            discovery = DirectoryDiscovery(self.staging_root, self.manifest_max_bytes)

            async def emit(progress: Any) -> None:
                await self._publish(batch_id, "progress", progress.model_dump(mode="json"))

            result = await discovery.discover(
                DiscoveryRequest(
                    batch_id=UUID(batch_id),
                    source_kind="staged_directory",
                    source_descriptor=descriptor,
                    repository_id=UUID(batch.repository_id) if batch.repository_id else None,
                ),
                emit,
            )
            if len(result.sources) > self.batch_max_items:
                raise DomainError("BATCH_LIMIT_EXCEEDED", "目录文件数量超过限制", 413)
            from app.storage.models import BatchItemRecord

            items = [
                BatchItemRecord(
                    source_identity=source.source_identity,
                    source_revision=source.source_revision,
                    title=source.title,
                    display_path=source.display_path,
                    media_type=source.media_type,
                    size_bytes=source.size_bytes,
                    cached_source_json=source.cached_source.model_dump(mode="json"),
                    remote_binding_json=(source.remote_binding.model_dump(mode="json") if source.remote_binding else None),
                    allowed_actions_json="[]",
                )
                for source in result.sources
            ]
            self.store.insert_discovered_items(batch_id, items)
            self.store.set_state(batch_id, BatchState.AWAITING_CONFIRMATION, message="目录发现完成")
        except asyncio.CancelledError:
            self.store.fail_discovery(batch_id, code="BATCH_APP_RESTARTED", message="应用重启导致目录发现中断", retryable=True)
            raise
        except DomainError as error:
            self.store.fail_discovery(batch_id, code=error.code, message=error.message, retryable=False)
        except Exception:  # noqa: BLE001 - discovery boundary maps ordinary failures
            self.store.fail_discovery(batch_id, code="IMPORT_FAILED", message="目录发现失败", retryable=False)

    async def ensure_terminal_event(self, batch_id: str) -> None:
        batch = self.get(batch_id)
        if self.event_broker is None or batch.state not in {
            BatchState.COMPLETED,
            BatchState.COMPLETED_WITH_ERRORS,
            BatchState.FAILED,
            BatchState.CANCELLED,
        }:
            return
        terminal = await self.event_broker.terminal(batch_id)
        if terminal is not None:
            return
        event_type: EventType = "error" if batch.state == BatchState.FAILED else "done"
        payload = {
            "progress": batch.progress,
            "state": batch.state.value,
            "message": batch.message,
        }
        if event_type == "error":
            payload.update({"code": batch.error_code, "retryable": batch.retryable})
        sequence = self.store.allocate_event_sequence(batch_id)
        await self.event_broker.publish(batch_id, event_type, payload, sequence=sequence)

    async def confirm(self, batch_id: str, confirmation) -> Any:  # type: ignore[no-untyped-def]
        batch = await asyncio.to_thread(
            self.store.confirm_and_reserve,
            batch_id,
            confirmation,
            reserve_child=self.import_service.reserve_batch_child,
            reserve_children=getattr(self.import_service, "reserve_batch_children", None),
            validate_sources=getattr(self.import_service, "validate_batch_sources", None),
        )
        event_type: EventType = "done" if batch.state == BatchState.COMPLETED else "progress"
        await self._publish(batch_id, event_type, self._progress_payload(batch))
        return batch

    async def continue_batch(self, batch_id: str) -> Any:
        lock = self._batch_locks.setdefault(batch_id, asyncio.Lock())
        async with lock:
            batch = self.get(batch_id)
            if batch.state in {BatchState.COMPLETED, BatchState.FAILED, BatchState.CANCELLED}:
                return batch
            if batch.state == BatchState.AWAITING_CONFIRMATION:
                raise DomainError("BATCH_STATE_CONFLICT", "批次尚未确认", 409)
            if batch.state == BatchState.PAUSED:
                batch = self.store.set_state(batch_id, BatchState.RUNNING, message="批次继续导入")
            elif batch.state == BatchState.COMPLETED_WITH_ERRORS:
                batch = self.store.set_state(batch_id, BatchState.RUNNING, message="批次重试中")

            tasks = self._batch_tasks.setdefault(batch_id, {})
            self._semaphores.setdefault(batch_id, asyncio.Semaphore(MAX_BATCH_CONCURRENCY))
            for item in self.store.list_item_records(batch_id):
                if (
                    item.state == BatchItemState.QUEUED
                    and item.import_job_id
                    and (item.id not in tasks or tasks[item.id].done())
                ):
                    task = asyncio.create_task(self._run_item(batch_id, item.id))
                    tasks[item.id] = task
                    task.add_done_callback(
                        lambda completed, item_id=item.id: self._discard_task(
                            batch_id, item_id, completed
                        )
                    )
            task_snapshot = list(tasks.values())
        if task_snapshot:
            await asyncio.gather(*task_snapshot, return_exceptions=True)
        return await self._aggregate(batch_id)

    async def cancel_batch(self, batch_id: str) -> Any:
        active_batches = self._cancel_context.get()
        if batch_id in active_batches:
            # Context is copied into child tasks.  Treat those calls as
            # re-entrant requests too, otherwise a callback that schedules a
            # nested cancel task can wait on the operation that waits on it.
            return self.get(batch_id)
        current_task = asyncio.current_task()
        if current_task is not None:
            in_flight = self._cancel_tasks.get(batch_id)
            if in_flight is current_task:
                return self.get(batch_id)
            if in_flight is not None:
                return await asyncio.shield(in_flight)
            self._cancel_tasks[batch_id] = current_task
        context_token = self._cancel_context.set(active_batches | {batch_id})
        try:
            return await self._cancel_batch_once(batch_id)
        finally:
            self._cancel_context.reset(context_token)
            if current_task is not None and self._cancel_tasks.get(batch_id) is current_task:
                self._cancel_tasks.pop(batch_id, None)

    async def _cancel_batch_once(self, batch_id: str) -> Any:
        lock = self._batch_locks.setdefault(batch_id, asyncio.Lock())
        async with lock:
            batch = self.store.request_cancel(batch_id)
            tasks = list(self._batch_tasks.get(batch_id, {}).values())
            child_ids: list[str] = []
            # Unsheduled children must never start after cancellation.
            for item in self.store.list_item_records(batch_id):
                if item.selected and item.state in {
                    BatchItemState.DISCOVERED,
                    BatchItemState.QUEUED,
                }:
                    self.store.mark_item_cancelled(item.id)
                elif item.state == BatchItemState.RUNNING and item.import_job_id:
                    child_ids.append(item.import_job_id)
        for job_id in child_ids:
            with suppress(Exception):
                await self.import_service.cancel(job_id)
        # Await outside the coordinator lock so cancellation can race with a
        # running continue_batch call without deadlocking.
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        async with lock:
            batch = self.store.finish_cancel(batch_id)
            await self._publish(batch_id, "done", self._progress_payload(batch))
            return batch

    async def retry_item(self, batch_id: str, item_id: str) -> Any:
        return await self.retry_items(batch_id, [item_id])

    async def retry_items(self, batch_id: str, item_ids: list[str] | None = None) -> Any:
        lock = self._batch_locks.setdefault(batch_id, asyncio.Lock())
        async with lock:
            batch = self.get(batch_id)
            if batch.state in {
                BatchState.COMPLETED,
                BatchState.FAILED,
                BatchState.CANCELLED,
            }:
                raise DomainError("BATCH_STATE_CONFLICT", "批次当前不可重试", 409)
            items = self.store.list_item_records(batch_id)
            by_id = {item.id: item for item in items}
            if item_ids is not None:
                for item_id in item_ids:
                    if item_id not in by_id:
                        raise DomainError("BATCH_ITEM_NOT_FOUND", "批次项不存在", 404)
                candidates = [by_id[item_id] for item_id in item_ids]
            else:
                candidates = items
            candidates = [item for item in candidates if item.state == BatchItemState.FAILED and item.retryable and item.import_job_id]
            if not candidates:
                raise DomainError("BATCH_STATE_CONFLICT", "批次项不可重试", 409)
            for item in candidates:
                await self.import_service.retry(item.import_job_id)
                self.store.mark_item_queued(item.id)
            if batch.state == BatchState.COMPLETED_WITH_ERRORS:
                self.store.set_state(batch_id, BatchState.RUNNING, message="批次重试中")
        return await self.continue_batch(batch_id)

    def recover_on_startup(self) -> int:
        return self.store.recover_on_startup()

    def _discard_task(
        self,
        batch_id: str,
        item_id: str,
        completed: asyncio.Task[None],
    ) -> None:
        tasks = self._batch_tasks.get(batch_id)
        if tasks is not None and tasks.get(item_id) is completed:
            tasks.pop(item_id, None)

    async def _run_item(self, batch_id: str, item_id: str) -> None:
        semaphore = self._semaphores.setdefault(batch_id, asyncio.Semaphore(MAX_BATCH_CONCURRENCY))
        async with semaphore:
            item = self.store.get_item(item_id)
            if item is None or item.import_job_id is None:
                return
            batch = self.store.get(batch_id)
            if batch is None or batch.cancel_requested:
                self.store.mark_item_cancelled(item_id)
                return
            self.store.mark_item_running(item_id)
            try:
                await self.import_service.run(item.import_job_id)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - child boundary maps all failures
                code, message, retryable = stable_error(error)
                self.store.mark_failed(item_id, code=code, message=message, retryable=retryable)
            else:
                with suppress(Exception):
                    child = self.import_service.get(item.import_job_id)
                    self.store.sync_child_terminal(item_id, child)
            await self._publish_progress(batch_id, item_id)

    async def _aggregate(self, batch_id: str) -> Any:
        batch = self.get(batch_id)
        items = self.store.list_item_records(batch_id)
        completed = sum(item.state == BatchItemState.COMPLETED for item in items)
        failed = sum(item.state == BatchItemState.FAILED for item in items)
        skipped = sum(item.state == BatchItemState.SKIPPED for item in items)
        finished = completed + failed + skipped + sum(item.state == BatchItemState.CANCELLED for item in items)
        progress = int(finished * 100 / len(items)) if items else 100
        batch = self.store.update_counts(
            batch_id,
            total_count=len(items),
            selected_count=sum(item.selected for item in items),
            completed_count=completed,
            failed_count=failed,
            skipped_count=skipped,
            progress=progress,
        )
        if batch.cancel_requested:
            target = BatchState.CANCELLED
        elif any(item.state in {BatchItemState.QUEUED, BatchItemState.RUNNING, BatchItemState.DISCOVERED} for item in items if item.selected):
            target = BatchState.RUNNING
        elif failed:
            target = BatchState.COMPLETED_WITH_ERRORS
        else:
            target = BatchState.COMPLETED
        if batch.state != target:
            batch = self.store.set_state(batch_id, target, message="批次完成" if target == BatchState.COMPLETED else None)
        await self._publish(batch_id, "done" if target in {BatchState.COMPLETED, BatchState.COMPLETED_WITH_ERRORS, BatchState.CANCELLED} else "progress", self._progress_payload(batch))
        return batch

    async def _publish_progress(self, batch_id: str, item_id: str) -> None:
        batch = self.get(batch_id)
        item = self.store.get_item(item_id)
        await self._publish(batch_id, "progress", self._progress_payload(batch, stage="import", item_id=item_id, item_state=item.state.value if item else None))

    async def _publish(self, batch_id: str, event_type: EventType, payload: dict[str, Any]) -> None:
        if self.event_broker is None:
            return
        sequence = self.store.allocate_event_sequence(batch_id)
        await self.event_broker.publish(batch_id, event_type, payload, sequence=sequence)

    @staticmethod
    def _progress_payload(batch: Any, *, stage: str = "batch", item_id: str | None = None, item_state: str | None = None) -> dict[str, Any]:
        return {
            "progress": batch.progress,
            "state": batch.state.value if hasattr(batch.state, "value") else str(batch.state),
            "message": batch.message,
            "stage": stage,
            "counts": {
                "total": batch.total_count,
                "selected": batch.selected_count,
                "completed": batch.completed_count,
                "failed": batch.failed_count,
                "skipped": batch.skipped_count,
            },
            "itemId": item_id,
            "itemState": item_state,
        }


def stable_error(error: Exception) -> tuple[str, str, bool]:
    code = getattr(error, "code", None)
    message = getattr(error, "message", None)
    retryable = getattr(error, "retryable", False)
    if isinstance(code, str) and code:
        return code, str(message or "批次子任务失败"), bool(retryable)
    return "IMPORT_FAILED", "批次子任务失败", False
