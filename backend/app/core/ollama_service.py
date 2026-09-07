from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx

from app.api.errors import DomainError
from app.core.ollama import PullCoordinator
from app.core.ollama_validation import normalize_loopback_base_url
from app.schemas.ollama import (
    OllamaConfig,
    OllamaModelsView,
    OllamaModelView,
    OllamaPullView,
    OllamaStatusView,
    validate_model_tag,
)
from app.storage.models import OllamaPullRecord
from app.storage.repositories import OllamaPullStore

HEALTH_CACHE_SECONDS = 5.0
UNAVAILABLE_MESSAGE = "Ollama 未运行或暂时无法连接"


@dataclass
class _HealthCache:
    checked_monotonic: float
    models: OllamaModelsView
    status: OllamaStatusView | None = None


async def run_pull_worker(
    service: OllamaService, stop_event: asyncio.Event, *, interval: float = 0.25
) -> None:
    while not stop_event.is_set():
        service.run_queued_once()
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            continue


class OllamaService:
    """Status, model and durable pull lifecycle boundary for Ollama."""

    def __init__(
        self,
        base_url: str | OllamaConfig | None = None,
        model: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 5,
        store: OllamaPullStore | None = None,
        coordinator: PullCoordinator | None = None,
        resolver=None,
        *,
        config: OllamaConfig | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if config is None and isinstance(base_url, OllamaConfig):
            config = base_url
            base_url = None
        if config is None:
            config = OllamaConfig(
                base_url=base_url or "http://127.0.0.1:11434",
                model=model,
                timeout_seconds=max(float(timeout), 0.001),
            )
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        self.model = config.model
        self.transport = transport
        self.timeout = config.timeout_seconds
        self.resolver = resolver
        self.store = store
        self._clock = clock or time.monotonic
        self._health: _HealthCache | None = None
        self._pulls: dict[UUID, OllamaPullView] = {}
        self._events: dict[UUID, list[dict[str, object]]] = {}
        self._tasks: dict[UUID, asyncio.Task[Any]] = {}
        self.coordinator = coordinator or PullCoordinator(
            self.config,
            transport=transport,
            timeout=max(self.timeout, 600),
            resolver=resolver,
        )
        if self.resolver is None:
            self.resolver = getattr(self.coordinator, "resolver", None)

    def invalidate_health(self) -> None:
        self._health = None

    def _cache_is_fresh(self) -> bool:
        return (
            self._health is not None
            and self._clock() - self._health.checked_monotonic < HEALTH_CACHE_SECONDS
        )

    async def _normalized_base_url(self) -> str:
        if self.resolver is None:
            return self.base_url
        self.base_url = await normalize_loopback_base_url(self.base_url, self.resolver)
        return self.base_url

    async def _fetch_json(self, path: str) -> dict[str, Any]:
        base_url = await self._normalized_base_url()
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, transport=self.transport
            ) as client:
                response = await client.get(f"{base_url}{path}")
        except (httpx.TimeoutException, httpx.HTTPError) as error:
            raise DomainError("OLLAMA_UNAVAILABLE", UNAVAILABLE_MESSAGE, 503, True) from error
        if response.status_code >= 400:
            raise DomainError("OLLAMA_UNAVAILABLE", UNAVAILABLE_MESSAGE, 503, True)
        try:
            payload = response.json()
        except (ValueError, TypeError) as error:
            raise DomainError(
                "OLLAMA_PROTOCOL_ERROR", "Ollama 返回了无法识别的数据", 502
            ) from error
        if not isinstance(payload, dict):
            raise DomainError("OLLAMA_PROTOCOL_ERROR", "Ollama 返回了无法识别的数据", 502)
        return payload

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    @classmethod
    def _normalize_models(cls, payload: dict[str, Any]) -> list[OllamaModelView]:
        rows = payload.get("models")
        if not isinstance(rows, list):
            raise DomainError("OLLAMA_PROTOCOL_ERROR", "Ollama 返回了无法识别的数据", 502)
        models: list[OllamaModelView] = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("name"), str):
                continue
            try:
                name = validate_model_tag(row["name"])
            except ValueError:
                continue
            size = row.get("size")
            size_bytes = (
                size
                if isinstance(size, int) and not isinstance(size, bool) and size >= 0
                else None
            )
            digest = row.get("digest") if isinstance(row.get("digest"), str) else None
            details = row.get("details")
            family = (
                details.get("family")
                if isinstance(details, dict) and isinstance(details.get("family"), str)
                else None
            )
            models.append(
                OllamaModelView(
                    name=name,
                    digest=digest,
                    size_bytes=size_bytes,
                    modified_at=cls._parse_datetime(row.get("modified_at")),
                    family=family,
                )
            )
        return models

    async def _refresh_models(self) -> OllamaModelsView:
        checked_at = datetime.now(UTC)
        try:
            models = self._normalize_models(await self._fetch_json("/api/tags"))
            view = OllamaModelsView(
                available=True,
                models=models,
                checked_at=checked_at,
                message="Ollama 已连接",
            )
        except DomainError as error:
            if error.code not in {"OLLAMA_UNAVAILABLE", "OLLAMA_PROTOCOL_ERROR"}:
                raise
            view = OllamaModelsView(
                available=False,
                models=[],
                checked_at=checked_at,
                message=UNAVAILABLE_MESSAGE,
            )
        self._health = _HealthCache(self._clock(), view, None)
        return view

    async def models(self) -> OllamaModelsView:
        if self._cache_is_fresh() and self._health is not None:
            return self._health.models
        return await self._refresh_models()

    async def status(self) -> OllamaStatusView:
        if (
            self._cache_is_fresh()
            and self._health is not None
            and self._health.status is not None
        ):
            return self._health.status
        models = await self.models()
        version: str | None = None
        if models.available:
            try:
                payload = await self._fetch_json("/api/version")
                if isinstance(payload.get("version"), str):
                    version = payload["version"]
            except DomainError:
                version = None
        status = OllamaStatusView(
            available=models.available,
            base_url=self.base_url,
            version=version,
            selected_model=self.model,
            selected_model_installed=models.available
            and self.model in {item.name for item in models.models},
            checked_at=models.checked_at,
            message=models.message,
        )
        if self._health is None:
            self._health = _HealthCache(self._clock(), models, status)
        else:
            self._health.status = status
        return status

    async def is_model_installed(self, model_name: str | None = None) -> bool:
        selected = model_name if model_name is not None else self.model
        models = await self.models()
        return models.available and selected in {item.name for item in models.models}

    async def preflight_model(self, model_name: str) -> None:
        models = await self.models()
        if not models.available:
            raise DomainError("OLLAMA_UNAVAILABLE", UNAVAILABLE_MESSAGE, 503, True)
        if model_name not in {item.name for item in models.models}:
            raise DomainError("OLLAMA_MODEL_NOT_INSTALLED", "选定模型尚未安装", 503, True)

    @staticmethod
    def _view_from_record(record: OllamaPullRecord) -> OllamaPullView:
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

    def _remember(self, view: OllamaPullView, *, event_type: str = "progress") -> OllamaPullView:
        self._pulls[view.id] = view
        self._events.setdefault(view.id, []).append(
            {
                "sequence": view.last_event_sequence,
                "type": event_type,
                "payload": view.model_dump(mode="json", by_alias=True),
            }
        )
        return view

    def create_pull(self, model_name: str) -> OllamaPullView:
        model_name = validate_model_tag(model_name)
        base_url = self.base_url.rstrip("/")
        if self.store is not None:
            existing = self.store.find_active(base_url)
        else:
            existing = next(
                (
                    item
                    for item in self._pulls.values()
                    if item.base_url == base_url and item.state in {"queued", "running"}
                ),
                None,
            )
        if existing is not None:
            if isinstance(existing, OllamaPullView):
                view = existing
            elif existing.model_name == model_name:
                view = self._view_from_record(existing)
            else:
                raise DomainError(
                    "OLLAMA_PULL_FAILED", "该 Ollama 地址已有活动拉取任务", 409, True
                )
            if view.model_name != model_name:
                raise DomainError(
                    "OLLAMA_PULL_FAILED", "该 Ollama 地址已有活动拉取任务", 409, True
                )
            self._pulls[view.id] = view
            self._events.setdefault(view.id, [])
            return view
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
            view = self._view_from_record(record)
        else:
            view = OllamaPullView(
                id=uuid4(),
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
        self._events[view.id] = []
        return self._remember(view)

    async def create_or_reuse_pull(self, model_name: str) -> OllamaPullView:
        return self.create_pull(model_name)

    def get_pull(self, pull_id: UUID) -> OllamaPullView:
        if self.store is not None:
            row = self.store.get(pull_id)
            if row is not None:
                view = self._view_from_record(row)
                self._pulls[pull_id] = view
                self._events.setdefault(pull_id, [])
                return view
        if pull_id in self._pulls:
            return self._pulls[pull_id]
        raise DomainError("OLLAMA_PULL_NOT_FOUND", "拉取任务不存在", 404)

    def retry_pull(self, pull_id: UUID) -> OllamaPullView:
        current = self.get_pull(pull_id)
        if current.state not in {"failed", "cancelled"} or not current.retryable:
            raise DomainError("OLLAMA_PULL_NOT_RETRYABLE", "该拉取任务不可重试", 409, False)
        return self.create_pull(current.model_name)

    def start_pull(self, pull_id: UUID) -> asyncio.Task[Any]:
        existing = self._tasks.get(pull_id)
        if existing is not None and not existing.done():
            return existing
        self.get_pull(pull_id)
        task = asyncio.create_task(self.execute_pull(pull_id, self.coordinator))
        self._tasks[pull_id] = task
        return task

    def run_queued_once(self) -> list[UUID]:
        if self.store is not None:
            queued = [UUID(row.id) for row in self.store.list_queued(base_url=self.base_url)]
        else:
            queued = [pull_id for pull_id, view in self._pulls.items() if view.state == "queued"]
        started: list[UUID] = []
        for pull_id in queued:
            self.start_pull(pull_id)
            started.append(pull_id)
        return started

    async def wait_for_pull(self, pull_id: UUID) -> OllamaPullView:
        task = self._tasks.get(pull_id)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        return self.get_pull(pull_id)

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

    async def execute_pull(
        self, pull_id: UUID, coordinator: PullCoordinator | None = None
    ) -> OllamaPullView:
        view = self.get_pull(pull_id)
        if view.state not in {"queued", "running"}:
            return view
        now = datetime.now(UTC)
        view = view.model_copy(
            update={
                "state": "running",
                "status": "准备下载",
                "started_at": view.started_at or now,
                "updated_at": now,
            }
        )
        view = self._record_pull_view(view)
        runner = coordinator or self.coordinator
        try:
            async for event in runner.pull(view.model_name):
                current = self.get_pull(pull_id)
                if current.cancel_requested or current.state == "cancelled":
                    return current
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
                        completed_at=datetime.now(UTC),
                        retryable=False,
                    )
                updates["updated_at"] = datetime.now(UTC)
                view = view.model_copy(update=updates)
                view = self._record_pull_view(view)
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
                view = self._record_pull_view(view)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - task owns provider boundary
            current = self.get_pull(pull_id)
            if current.state == "cancelled":
                return current
            view = current.model_copy(
                update={
                    "state": "failed",
                    "status": "拉取失败",
                    "error_code": getattr(error, "code", "OLLAMA_PULL_FAILED"),
                    "error_message": "模型拉取失败",
                    "retryable": True,
                    "completed_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                }
            )
            view = self._record_pull_view(view, event_type="error")
        self.invalidate_health()
        return view

    def _record_pull_view(
        self, view: OllamaPullView, *, event_type: str = "progress"
    ) -> OllamaPullView:
        sequence = view.last_event_sequence + 1
        view = view.model_copy(update={"last_event_sequence": sequence})
        self._pulls[view.id] = view
        self._events.setdefault(view.id, []).append(
            {
                "sequence": sequence,
                "type": event_type,
                "payload": {
                    **view.model_dump(mode="json", by_alias=True),
                    **(
                        {
                            "code": view.error_code or "OLLAMA_PULL_FAILED",
                            "message": view.error_message or "模型拉取失败",
                            "retryable": view.retryable,
                        }
                        if event_type == "error"
                        else {}
                    ),
                },
            }
        )
        if self.store is not None:
            self.store.update(
                view.id,
                **{
                    field: getattr(view, field)
                    for field in (
                        "model_name",
                        "base_url",
                        "state",
                        "progress",
                        "status",
                        "total_bytes",
                        "completed_bytes",
                        "error_code",
                        "error_message",
                        "retryable",
                        "cancel_requested",
                        "created_at",
                        "started_at",
                        "completed_at",
                        "updated_at",
                        "last_event_sequence",
                    )
                },
            )
        return view

    def cancel_pull(self, pull_id: UUID) -> OllamaPullView:
        task = self._tasks.get(pull_id)
        if task is not None and not task.done():
            task.cancel()
        view = self.get_pull(pull_id)
        if view.state not in {"queued", "running"}:
            return view
        view = view.model_copy(
            update={
                "state": "cancelled",
                "status": "已取消",
                "cancel_requested": True,
                "error_code": "OLLAMA_PULL_CANCELLED",
                "error_message": "模型拉取已取消",
                "completed_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
                "retryable": True,
            }
        )
        view = self._record_pull_view(view, event_type="error")
        self.invalidate_health()
        return view

    def recover_on_startup(self) -> None:
        if self.store is not None:
            self.store.recover_on_startup()
        self._tasks.clear()
