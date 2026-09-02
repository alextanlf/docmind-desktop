from __future__ import annotations

import asyncio
import inspect
from contextlib import suppress
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
        self._batch_tasks: dict[str, set[asyncio.Task[None]]] = {}

    def get(self, batch_id: str):
        batch = self.store.get(batch_id)
        if batch is None:
            raise DomainError("BATCH_NOT_FOUND", "批次不存在", 404)
        return batch

    async def confirm(self, batch_id: str, confirmation) -> Any:  # type: ignore[no-untyped-def]
        lock = self._batch_locks.setdefault(batch_id, asyncio.Lock())
        async with lock:
            batch = self.get(batch_id)
            # set_confirmation performs the row-level state/version/decision
            # transaction.  No child is reserved before it succeeds.
            # Validate the discovered snapshot before persisting decisions so
            # a stale confirmation leaves the batch awaiting confirmation.
            self._validate_snapshot(self.store.list_item_records(batch_id))
            batch = self.store.set_confirmation(batch_id, confirmation)
            items = self.store.list_item_records(batch_id)
            for item in items:
                if item.selected and item.state == BatchItemState.DISCOVERED and item.import_job_id is None:
                    child_id = await self._reserve_child(item, batch.repository_id)
                    self.store.reserve_child_job(item.id, child_id)
            if batch.selected_count == 0:
                batch = self.store.set_state(batch_id, BatchState.COMPLETED, message="批次无待导入项")
            else:
                batch = self.store.set_state(batch_id, BatchState.RUNNING, message="批次导入中")
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

            # A retry or a recovered batch may have selected items not yet
            # reserved.  Reservation is idempotent at the item row boundary.
            for item in self.store.list_item_records(batch_id):
                if item.selected and item.state in {BatchItemState.DISCOVERED, BatchItemState.QUEUED} and item.import_job_id is None:
                    child_id = await self._reserve_child(item, batch.repository_id)
                    self.store.reserve_child_job(item.id, child_id)

            tasks = self._batch_tasks.setdefault(batch_id, set())
            if not tasks:
                self._semaphores.setdefault(batch_id, asyncio.Semaphore(MAX_BATCH_CONCURRENCY))
                for item in self.store.list_item_records(batch_id):
                    if item.state == BatchItemState.QUEUED and item.import_job_id:
                        task = asyncio.create_task(self._run_item(batch_id, item.id))
                        tasks.add(task)
                        task.add_done_callback(tasks.discard)
        if tasks:
            await asyncio.gather(*list(tasks), return_exceptions=True)
        return await self._aggregate(batch_id)

    async def cancel_batch(self, batch_id: str) -> Any:
        lock = self._batch_locks.setdefault(batch_id, asyncio.Lock())
        async with lock:
            batch = self.store.request_cancel(batch_id)
            tasks = list(self._batch_tasks.get(batch_id, set()))
            # Unsheduled children must never start after cancellation.
            for item in self.store.list_item_records(batch_id):
                if item.state == BatchItemState.QUEUED:
                    self.store.mark_item_cancelled(item.id)
                elif item.state == BatchItemState.RUNNING and item.import_job_id:
                    with suppress(Exception):
                        await self.import_service.cancel(item.import_job_id)
        # Await outside the coordinator lock so cancellation can race with a
        # running continue_batch call without deadlocking.
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        async with lock:
            for item in self.store.list_item_records(batch_id):
                if item.state in {BatchItemState.QUEUED, BatchItemState.RUNNING}:
                    self.store.mark_item_cancelled(item.id)
            batch = self.store.get(batch_id) or batch
            if batch.state not in {BatchState.COMPLETED, BatchState.COMPLETED_WITH_ERRORS, BatchState.FAILED, BatchState.CANCELLED}:
                batch = self.store.set_state(batch_id, BatchState.CANCELLED, message="批次已取消")
            await self._publish(batch_id, "done", self._progress_payload(batch))
            return batch

    async def retry_item(self, batch_id: str, item_id: str) -> Any:
        lock = self._batch_locks.setdefault(batch_id, asyncio.Lock())
        async with lock:
            batch = self.get(batch_id)
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

    async def _reserve_child(self, item: Any, repository_id: str | None) -> str:
        reserve = self.import_service.reserve_batch_child
        try:
            signature = inspect.signature(reserve)
            if "repository_id" in signature.parameters:
                return await reserve(item, repository_id=repository_id)
        except (TypeError, ValueError):
            pass
        return await reserve(item)

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

    def _validate_snapshot(self, items: list[Any]) -> None:
        """Reject confirmations whose discovered document snapshot is stale."""
        if self.document_store is None:
            return
        for item in items:
            document = None
            if item.existing_document_id:
                document = self.document_store.get(item.existing_document_id)
                if document is None:
                    raise DomainError("BATCH_DISCOVERY_CONFLICT", "文档已被删除，请重新发现", 409)
            elif hasattr(self.document_store, "find_by_source"):
                repository_id = self.get(item.batch_id).repository_id
                document = self.document_store.find_by_source(repository_id or "", item.source_identity)
            if document is not None:
                current_hash = getattr(document, "content_hash", None) or getattr(document, "source_revision", None)
                expected_hash = item.source_revision.removeprefix("sha256:")
                if current_hash and expected_hash and current_hash.removeprefix("sha256:") != expected_hash:
                    raise DomainError("BATCH_DISCOVERY_CONFLICT", "文档内容已变更，请重新发现", 409)

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
