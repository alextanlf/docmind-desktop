from __future__ import annotations

import asyncio
import json
import math
import socket
import time
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx

from app.api.errors import DomainError
from app.core.llm import ChatDelta, ChatRequest, ModelConnectionResult
from app.core.ollama_validation import normalize_loopback_base_url
from app.imports.events import EventEnvelope
from app.schemas.ollama import OllamaConfig, OllamaPullView, validate_model_tag
from app.storage.models import OllamaPullRecord
from app.storage.repositories import OllamaPullStore

_UNAVAILABLE_MESSAGE = "Ollama 未运行或暂时无法连接"
_PROTOCOL_MESSAGE = "Ollama 返回了无法识别的数据"
_MAX_LINE_BYTES = 1 << 20
_CHAT_DEADLINE_SECONDS = 10 * 60


class _SystemResolver:
    async def resolve(self, host: str):
        rows = await asyncio.to_thread(
            socket.getaddrinfo, host, 11434, type=socket.SOCK_STREAM
        )
        return sorted({row[4][0] for row in rows})


def _ollama_http_error(status_code: int, *, tags: bool = False) -> DomainError:
    if status_code == 404 and not tags:
        return DomainError("OLLAMA_MODEL_NOT_INSTALLED", "选定模型尚未安装", 503, True)
    if status_code >= 500 or tags:
        return DomainError("OLLAMA_UNAVAILABLE", _UNAVAILABLE_MESSAGE, 503, True)
    return DomainError("OLLAMA_PROTOCOL_ERROR", _PROTOCOL_MESSAGE, 502, False)


def _protocol_error() -> DomainError:
    return DomainError("OLLAMA_PROTOCOL_ERROR", _PROTOCOL_MESSAGE, 502, False)


def _unavailable_error(cause: BaseException | None = None) -> DomainError:
    error = DomainError("OLLAMA_UNAVAILABLE", _UNAVAILABLE_MESSAGE, 503, True)
    if cause is not None:
        error.__cause__ = cause
    return error


async def _bounded_lines(
    response: httpx.Response,
    *,
    max_bytes: int = _MAX_LINE_BYTES,
    deadline_seconds: float | None = None,
) -> AsyncIterator[str]:
    iterator = response.aiter_lines().__aiter__()
    deadline = None if deadline_seconds is None else time.monotonic() + deadline_seconds
    while True:
        try:
            if deadline is None:
                line = await iterator.__anext__()
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                line = await asyncio.wait_for(iterator.__anext__(), timeout=remaining)
        except StopAsyncIteration:
            return
        except TimeoutError as error:
            raise _unavailable_error(error) from error
        if len(line.encode("utf-8", errors="replace")) > max_bytes:
            raise _protocol_error()
        yield line


class OllamaProvider:
    def __init__(
        self,
        config_or_base_url: OllamaConfig | str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver=None,
        *,
        config: OllamaConfig | None = None,
    ) -> None:
        if config is None and isinstance(config_or_base_url, OllamaConfig):
            config = config_or_base_url
            config_or_base_url = None
        if config is None:
            config = OllamaConfig(
                base_url=config_or_base_url or "http://127.0.0.1:11434",
                model=model or "",
                timeout_seconds=120.0 if timeout is None else timeout,
            )
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        self.model = config.model
        self.timeout = config.timeout_seconds
        if self.model:
            validate_model_tag(self.model)
        self.transport = transport
        self.resolver = resolver or _SystemResolver()

    async def _base(self) -> str:
        self.base_url = await normalize_loopback_base_url(self.base_url, self.resolver)
        return self.base_url

    async def test_connection(self) -> ModelConnectionResult:
        started = time.perf_counter()
        try:
            base = await self._base()
            async with httpx.AsyncClient(
                timeout=self.timeout, transport=self.transport
            ) as client:
                response = await client.get(f"{base}/api/tags")
            if response.status_code >= 400:
                raise _ollama_http_error(response.status_code, tags=True)
        except DomainError:
            raise
        except (httpx.TimeoutException, httpx.HTTPError) as error:
            raise _unavailable_error(error) from error
        return ModelConnectionResult(
            connected=True,
            latency_ms=round((time.perf_counter() - started) * 1000),
        )

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatDelta]:
        payload = {
            "model": self.model,
            "messages": [message.model_dump() for message in request.messages],
            "stream": True,
            "options": {"temperature": request.temperature},
        }
        emitted = False
        saw_done = False
        try:
            base = await self._base()
            async with (
                httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client,
                client.stream("POST", f"{base}/api/chat", json=payload) as response,
            ):
                if response.status_code >= 400:
                    raise _ollama_http_error(response.status_code)
                async for line in _bounded_lines(
                    response, deadline_seconds=_CHAT_DEADLINE_SECONDS
                ):
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except (json.JSONDecodeError, UnicodeDecodeError) as error:
                        raise _protocol_error() from error
                    if not isinstance(item, dict) or saw_done:
                        raise _protocol_error()
                    if item.get("error") is not None:
                        raise _protocol_error()
                    message = item.get("message")
                    if message is not None and not isinstance(message, dict):
                        raise _protocol_error()
                    content = message.get("content") if isinstance(message, dict) else None
                    if content is not None and not isinstance(content, str):
                        raise _protocol_error()
                    if content:
                        emitted = True
                        yield ChatDelta(content=content)
                    done = item.get("done", False)
                    if done is not False and done is not True:
                        raise _protocol_error()
                    if done:
                        saw_done = True
                if not emitted or not saw_done:
                    raise _protocol_error()
        except DomainError:
            raise
        except (httpx.TimeoutException, httpx.HTTPError) as error:
            raise _unavailable_error(error) from error


class PullCoordinator:
    """Project Ollama pull NDJSON into durable snapshots and event envelopes."""

    def __init__(
        self,
        base_url: OllamaConfig | str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 600,
        resolver=None,
        *,
        config: OllamaConfig | None = None,
        store: OllamaPullStore | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if config is None and isinstance(base_url, OllamaConfig):
            config = base_url
            base_url = None
        if config is None:
            config = OllamaConfig(
                base_url=base_url or "http://127.0.0.1:11434",
                timeout_seconds=max(float(timeout), 0.001),
            )
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        self.transport = transport
        self.timeout = config.timeout_seconds if timeout == 600 else timeout
        self.resolver = resolver or _SystemResolver()
        self.store = store
        self._clock = clock or time.monotonic
        self._tasks: dict[UUID, asyncio.Task[Any]] = {}
        self._events: dict[UUID, list[EventEnvelope]] = {}
        self._conditions: dict[UUID, asyncio.Condition] = {}
        self._views: dict[UUID, OllamaPullView] = {}

    async def _base(self) -> str:
        self.base_url = await normalize_loopback_base_url(self.base_url, self.resolver)
        return self.base_url

    @staticmethod
    def _view(record: OllamaPullRecord) -> OllamaPullView:
        return OllamaPullView(
            id=UUID(str(record.id)),
            model_name=record.model_name,
            base_url=record.base_url,
            state=record.state,
            progress=max(0, min(100, int(record.progress or 0))),
            status=record.status,
            total_bytes=record.total_bytes,
            completed_bytes=record.completed_bytes,
            error_code=record.error_code,
            error_message=record.error_message,
            retryable=bool(record.retryable),
            cancel_requested=bool(record.cancel_requested),
            last_event_sequence=max(0, int(record.last_event_sequence or 0)),
            created_at=record.created_at,
            started_at=record.started_at,
            completed_at=record.completed_at,
            updated_at=record.updated_at,
        )

    def _get_view(self, pull_id: UUID) -> OllamaPullView:
        if self.store is not None:
            record = self.store.get(pull_id)
            if record is not None:
                view = self._view(record)
                self._views[pull_id] = view
                return view
        view = self._views.get(pull_id)
        if view is None:
            raise DomainError("OLLAMA_PULL_NOT_FOUND", "拉取任务不存在", 404)
        return view

    def get(self, pull_id: UUID) -> OllamaPullView:
        return self._get_view(pull_id)

    def get_pull(self, pull_id: UUID) -> OllamaPullView:
        return self._get_view(pull_id)

    async def start(self, model_name: str) -> OllamaPullView:
        model_name = validate_model_tag(model_name)
        base_url = await self._base()
        existing = self.store.find_active(base_url) if self.store is not None else next(
            (
                view
                for view in self._views.values()
                if view.base_url == base_url and view.state in {"queued", "running"}
            ),
            None,
        )
        if existing is not None:
            existing_view = existing if isinstance(existing, OllamaPullView) else self._view(existing)
            if existing_view.model_name == model_name:
                return existing_view
            raise DomainError("OLLAMA_PULL_FAILED", "该 Ollama 地址已有活动拉取任务", 409, True)
        now = datetime.now(UTC)
        if self.store is not None:
            record = self.store.create(
                model_name=model_name,
                base_url=base_url,
                state="queued",
                status="排队中",
                progress=0,
                retryable=True,
                cancel_requested=False,
                last_event_sequence=0,
                created_at=now,
                updated_at=now,
            )
            view = self._view(record)
        else:
            view = OllamaPullView(
                id=UUID(int=0),
                model_name=model_name,
                base_url=base_url,
                state="queued",
                progress=0,
                status="排队中",
                retryable=True,
                cancel_requested=False,
                last_event_sequence=0,
                created_at=now,
                updated_at=now,
            )
        if view.id.int == 0:
            from uuid import uuid4

            view = view.model_copy(update={"id": uuid4()})
        self._views[view.id] = view
        self._events.setdefault(view.id, [])
        self._conditions.setdefault(view.id, asyncio.Condition())
        self._tasks[view.id] = asyncio.create_task(self._run(view.id))
        return view

    async def _persist_event(
        self, pull_id: UUID, view: OllamaPullView, *, event_type: str
    ) -> OllamaPullView:
        sequence = view.last_event_sequence + 1
        view = view.model_copy(update={"last_event_sequence": sequence})
        if self.store is not None:
            self.store.update(
                pull_id,
                **{
                    field: getattr(view, field)
                    for field in (
                        "model_name", "base_url", "state", "progress", "status",
                        "total_bytes", "completed_bytes", "error_code", "error_message",
                        "retryable", "cancel_requested", "created_at", "started_at",
                        "completed_at", "updated_at", "last_event_sequence",
                    )
                },
            )
        self._views[pull_id] = view
        payload = view.model_dump(mode="json", by_alias=True)
        if event_type == "error":
            payload.update(
                {
                    "code": view.error_code or "OLLAMA_PULL_FAILED",
                    "message": view.error_message or "模型拉取失败",
                    "retryable": view.retryable,
                }
            )
        envelope = EventEnvelope(
            request_id=pull_id,
            type=event_type,  # type: ignore[arg-type]
            sequence=sequence,
            payload=payload,
        )
        self._events.setdefault(pull_id, []).append(envelope)
        condition = self._conditions.setdefault(pull_id, asyncio.Condition())
        async with condition:
            condition.notify_all()
        return view

    async def _run(self, pull_id: UUID) -> None:
        view = self._get_view(pull_id)
        view = view.model_copy(
            update={
                "state": "running",
                "status": "准备下载",
                "started_at": view.started_at or datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            }
        )
        view = await self._persist_event(pull_id, view, event_type="progress")
        try:
            async for event in self.pull(view.model_name):
                current = self._get_view(pull_id)
                if current.cancel_requested or current.state == "cancelled":
                    return
                updates: dict[str, Any] = {
                    key: event[key]
                    for key in ("progress", "status", "total_bytes", "completed_bytes")
                    if key in event
                }
                if "progress" in updates:
                    updates["progress"] = max(0, min(100, int(updates["progress"])))
                if event.get("state") == "completed":
                    updates.update(
                        state="completed",
                        progress=100,
                        retryable=False,
                        completed_at=datetime.now(UTC),
                    )
                view = current.model_copy(update={**updates, "updated_at": datetime.now(UTC)})
                view = await self._persist_event(pull_id, view, event_type="progress")
            if view.state == "running":
                view = view.model_copy(
                    update={
                        "state": "completed",
                        "status": "完成",
                        "progress": 100,
                        "retryable": False,
                        "completed_at": datetime.now(UTC),
                        "updated_at": datetime.now(UTC),
                    }
                )
                await self._persist_event(pull_id, view, event_type="done")
        except asyncio.CancelledError:
            # ``cancel`` records the terminal snapshot synchronously; do not
            # overwrite it when task cancellation closes the HTTP response.
            return
        except Exception as error:  # noqa: BLE001 - normalize upstream boundary
            current = self._get_view(pull_id)
            if current.state == "cancelled":
                return
            failed = current.model_copy(
                update={
                    "state": "failed",
                    "status": "拉取失败",
                    "error_code": getattr(error, "code", "OLLAMA_PULL_FAILED"),
                    "error_message": "模型拉取失败",
                    "retryable": getattr(error, "retryable", True),
                    "completed_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                }
            )
            await self._persist_event(pull_id, failed, event_type="error")

    async def cancel(self, pull_id: UUID) -> OllamaPullView:
        view = self._get_view(pull_id)
        if view.state not in {"queued", "running"}:
            return view
        cancelled = view.model_copy(
            update={
                "state": "cancelled",
                "status": "已取消",
                "cancel_requested": True,
                "error_code": "OLLAMA_PULL_CANCELLED",
                "error_message": "模型拉取已取消",
                "retryable": True,
                "completed_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            }
        )
        cancelled = await self._persist_event(pull_id, cancelled, event_type="error")
        task = self._tasks.get(pull_id)
        if task is not None and not task.done():
            task.cancel()
        return cancelled

    def subscribe(self, pull_id: UUID, after_sequence: int = 0) -> AsyncIterator[EventEnvelope]:
        cursor = max(0, int(after_sequence))
        condition = self._conditions.setdefault(pull_id, asyncio.Condition())

        async def stream() -> AsyncIterator[EventEnvelope]:
            nonlocal cursor
            while True:
                events = [
                    event
                    for event in self._events.get(pull_id, [])
                    if event.sequence > cursor
                ]
                if events:
                    for event in events:
                        cursor = event.sequence
                        yield event
                        if event.type in {"done", "error"}:
                            return
                    continue
                try:
                    view = self._get_view(pull_id)
                except DomainError:
                    return
                if view.state in {"completed", "failed", "cancelled"}:
                    # A subscriber joining after retention was lost still gets
                    # a safe terminal snapshot, with a fresh sequence only if
                    # the snapshot has not already been replayed.
                    if view.last_event_sequence > cursor:
                        envelope = EventEnvelope(
                            request_id=pull_id,
                            type="done" if view.state == "completed" else "error",
                            sequence=view.last_event_sequence,
                            payload=view.model_dump(mode="json", by_alias=True),
                        )
                        cursor = envelope.sequence
                        yield envelope
                    return
                async with condition:
                    await condition.wait()

        return stream()

    async def wait_for_idle(self) -> None:
        tasks = tuple(self._tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def stop(self) -> None:
        tasks = tuple(task for task in self._tasks.values() if not task.done())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def recover_on_startup(self) -> None:
        if self.store is not None:
            self.store.recover_on_startup()

    async def pull(self, model_name: str) -> AsyncIterator[dict[str, object]]:
        model_name = validate_model_tag(model_name)
        try:
            base = await self._base()
            async with (
                httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client,
                client.stream(
                    "POST",
                    f"{base}/api/pull",
                    json={"name": model_name, "stream": True},
                ) as response,
            ):
                if response.status_code >= 400:
                    raise DomainError("OLLAMA_PULL_FAILED", "模型拉取失败", 502, True)
                previous_progress = 0
                async for line in _bounded_lines(response, deadline_seconds=10 * 60):
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except (json.JSONDecodeError, UnicodeDecodeError) as error:
                        raise _protocol_error() from error
                    if not isinstance(item, dict):
                        raise _protocol_error()
                    if item.get("error") is not None:
                        raise DomainError("OLLAMA_PULL_FAILED", "模型拉取失败", 502, True)
                    status = item.get("status")
                    event: dict[str, object] = {
                        "status": status if isinstance(status, str) else "正在下载"
                    }
                    total, completed = item.get("total"), item.get("completed")
                    if isinstance(total, (int, float)) and not isinstance(total, bool) and total > 0 and isinstance(completed, (int, float)) and not isinstance(completed, bool):
                        previous_progress = max(
                            0, min(100, math.floor(float(completed) * 100 / float(total)))
                        )
                        event.update(
                            progress=previous_progress,
                            total_bytes=max(0, int(total)),
                            completed_bytes=max(0, int(completed)),
                        )
                    else:
                        event["progress"] = previous_progress
                    if item.get("done") is True or status == "success":
                        event["state"] = "completed"
                    yield event
        except DomainError:
            raise
        except (httpx.TimeoutException, httpx.HTTPError) as error:
            raise DomainError("OLLAMA_PULL_FAILED", "模型拉取失败", 502, True) from error
