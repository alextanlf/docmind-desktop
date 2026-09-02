from __future__ import annotations

import asyncio
from contextlib import suppress
from contextvars import ContextVar
from typing import Any

from app.api.errors import DomainError
from app.imports.events import EventType, ImportEventBroker
from app.storage.models import BatchItemState, BatchState
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
    ) -> None:
        if max_concurrency != MAX_BATCH_CONCURRENCY:
            raise ValueError("batch concurrency is fixed at three")
        self.store = store
        self.import_service = import_service
        self.document_store = document_store
        self.event_broker = event_broker
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
        lock = self._batch_locks.setdefault(batch_id, asyncio.Lock())
        async with lock:
            batch = self.get(batch_id)
            if batch.state in {
                BatchState.COMPLETED,
                BatchState.FAILED,
                BatchState.CANCELLED,
            }:
                raise DomainError("BATCH_STATE_CONFLICT", "批次当前不可重试", 409)
            item = self.store.get_item(item_id)
            if item is None or item.batch_id != batch_id:
                raise DomainError("BATCH_ITEM_NOT_FOUND", "批次项不存在", 404)
            if item.state != BatchItemState.FAILED or not item.retryable or not item.import_job_id:
                raise DomainError("BATCH_STATE_CONFLICT", "批次项不可重试", 409)
            await self.import_service.retry(item.import_job_id)
            self.store.mark_item_queued(item_id)
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
        del item_id
        batch = self.get(batch_id)
        await self._publish(batch_id, "progress", self._progress_payload(batch))

    async def _publish(self, batch_id: str, event_type: EventType, payload: dict[str, Any]) -> None:
        if self.event_broker is None:
            return
        sequence = self.store.allocate_event_sequence(batch_id)
        await self.event_broker.publish(batch_id, event_type, payload, sequence=sequence)

    @staticmethod
    def _progress_payload(batch: Any) -> dict[str, Any]:
        return {
            "progress": batch.progress,
            "state": batch.state.value if hasattr(batch.state, "value") else str(batch.state),
            "message": batch.message,
        }


def stable_error(error: Exception) -> tuple[str, str, bool]:
    code = getattr(error, "code", None)
    message = getattr(error, "message", None)
    retryable = getattr(error, "retryable", False)
    if isinstance(code, str) and code:
        return code, str(message or "批次子任务失败"), bool(retryable)
    return "IMPORT_FAILED", "批次子任务失败", False
