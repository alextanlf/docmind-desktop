from __future__ import annotations
from datetime import UTC, datetime
from uuid import UUID, uuid4
import httpx
from app.schemas.ollama import OllamaModelView, OllamaModelsView, OllamaPullView
from app.storage.repositories import OllamaPullStore
from app.storage.models import OllamaPullRecord

class OllamaService:
    def __init__(self, base_url: str, model: str = "", transport: httpx.AsyncBaseTransport | None = None, timeout: float = 5, store: OllamaPullStore | None = None) -> None:
        self.base_url, self.model, self.transport, self.timeout = base_url.rstrip("/"), model, transport, timeout
        self._pulls: dict[UUID, OllamaPullView] = {}
        self._events: dict[UUID, list[dict[str, object]]] = {}
        self.store = store

    async def models(self) -> OllamaModelsView:
        checked = datetime.now(UTC)
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                payload = response.json()
            models = [OllamaModelView(name=item["name"], digest=item.get("digest"), size_bytes=item.get("size"), modified_at=item.get("modified_at"), family=item.get("details", {}).get("family")) for item in payload.get("models", []) if isinstance(item, dict) and isinstance(item.get("name"), str)]
            return OllamaModelsView(available=True, models=models, checked_at=checked, message="Ollama 已连接")
        except Exception:
            return OllamaModelsView(available=False, models=[], checked_at=checked, message="Ollama 未运行或暂时无法连接")

    async def status(self):
        models = await self.models()
        names = {item.name for item in models.models}
        from app.schemas.ollama import OllamaStatusView
        return OllamaStatusView(available=models.available, base_url=self.base_url, selected_model=self.model, selected_model_installed=self.model in names, checked_at=models.checked_at, message=models.message)

    def create_pull(self, model_name: str) -> OllamaPullView:
        for existing in self._pulls.values():
            if existing.model_name == model_name and existing.base_url == self.base_url and existing.state in {"queued", "running"}:
                return existing
        if self.store:
            existing = self.store.find_active(model_name=model_name, base_url=self.base_url)
            if existing:
                view = OllamaPullView(id=UUID(existing.id), model_name=existing.model_name, base_url=existing.base_url, state=existing.state, progress=existing.progress, status=existing.status, total_bytes=existing.total_bytes, completed_bytes=existing.completed_bytes, error_code=existing.error_code, error_message=existing.error_message, retryable=existing.retryable, cancel_requested=existing.cancel_requested, last_event_sequence=existing.last_event_sequence, created_at=existing.created_at, started_at=existing.started_at, completed_at=existing.completed_at, updated_at=existing.updated_at)
                self._pulls[view.id] = view
                self._events.setdefault(view.id, [{"sequence": 0, "type": "progress", "payload": view.model_dump(mode="json", by_alias=True)}])
                return view
        now = datetime.now(UTC); pull_id = uuid4()
        view = OllamaPullView(id=pull_id, model_name=model_name, base_url=self.base_url, state="queued", progress=0, status="排队中", retryable=True, created_at=now, updated_at=now)
        self._pulls[pull_id] = view
        self._events[pull_id] = [{"sequence": 0, "type": "progress", "payload": view.model_dump(mode="json", by_alias=True)}]
        if self.store:
            self.store.save(OllamaPullRecord(id=str(pull_id), model_name=model_name, base_url=self.base_url, state="queued", progress=0, status="排队中", created_at=now, updated_at=now))
        return view

    def get_pull(self, pull_id: UUID) -> OllamaPullView:
        if self.store:
            row = self.store.get(str(pull_id))
            if row:
                return OllamaPullView(id=pull_id, model_name=row.model_name, base_url=row.base_url, state=row.state, progress=row.progress, status=row.status, total_bytes=row.total_bytes, completed_bytes=row.completed_bytes, error_code=row.error_code, error_message=row.error_message, retryable=row.retryable, cancel_requested=row.cancel_requested, last_event_sequence=row.last_event_sequence, created_at=row.created_at, started_at=row.started_at, completed_at=row.completed_at, updated_at=row.updated_at)
        from app.api.errors import DomainError
        try: return self._pulls[pull_id]
        except KeyError as error: raise DomainError("OLLAMA_PULL_NOT_FOUND", "拉取任务不存在", 404) from error

    def retry_pull(self, pull_id: UUID) -> OllamaPullView:
        from app.api.errors import DomainError
        current = self.get_pull(pull_id)
        if current.state not in {"failed", "cancelled"} or not current.retryable:
            raise DomainError("OLLAMA_PULL_NOT_RETRYABLE", "该拉取任务不可重试", 409, False)
        self._pulls.pop(pull_id, None)
        return self.create_pull(current.model_name)

    async def execute_pull(self, pull_id: UUID, coordinator) -> OllamaPullView:
        """Consume a coordinator stream and project only safe snapshots."""
        view = self.get_pull(pull_id)
        if view.state not in {"queued", "running"}:
            return view
        now = datetime.now(UTC)
        view = view.model_copy(update={"state": "running", "status": "准备下载", "started_at": view.started_at or now, "updated_at": now})
        view = self._record_pull_view(view)
        try:
            async for event in coordinator.pull(view.model_name):
                if self.get_pull(pull_id).cancel_requested:
                    return self.get_pull(pull_id)
                updates = {key: event[key] for key in ("progress", "status", "total_bytes", "completed_bytes") if key in event}
                if event.get("state") == "completed":
                    updates.update(state="completed", completed_at=datetime.now(UTC), retryable=False)
                updates["updated_at"] = datetime.now(UTC)
                view = view.model_copy(update=updates)
                view = self._record_pull_view(view)
            if view.state == "running":
                view = view.model_copy(update={"state": "completed", "status": "完成", "progress": 100, "retryable": False, "completed_at": datetime.now(UTC), "updated_at": datetime.now(UTC)})
                view = self._record_pull_view(view)
        except Exception as error:
            view = view.model_copy(update={"state": "failed", "status": "拉取失败", "error_code": getattr(error, "code", "OLLAMA_PULL_FAILED"), "error_message": "模型拉取失败", "retryable": True, "updated_at": datetime.now(UTC)})
            view = self._record_pull_view(view)
        return view

    def _record_pull_view(self, view: OllamaPullView) -> OllamaPullView:
        sequence = view.last_event_sequence + 1
        view = view.model_copy(update={"last_event_sequence": sequence})
        self._pulls[view.id] = view
        self._events.setdefault(view.id, []).append({"sequence": sequence, "type": "progress", "payload": view.model_dump(mode="json", by_alias=True)})
        if self.store:
            row = self.store.get(str(view.id))
            if row:
                for field in ("model_name", "base_url", "state", "progress", "status", "total_bytes", "completed_bytes", "error_code", "error_message", "retryable", "cancel_requested", "created_at", "started_at", "completed_at", "updated_at", "last_event_sequence"):
                    setattr(row, field, getattr(view, field))
                self.store.save(row)
        return view

    def cancel_pull(self, pull_id: UUID) -> OllamaPullView:
        view = self.get_pull(pull_id)
        if view.state in {"queued", "running"}:
            view = view.model_copy(update={"state":"cancelled", "status":"已取消", "cancel_requested":True, "error_code":"OLLAMA_PULL_CANCELLED", "last_event_sequence": view.last_event_sequence + 1, "updated_at":datetime.now(UTC)})
            self._pulls[pull_id] = view
            self._events.setdefault(pull_id, []).append({"sequence": view.last_event_sequence + 1, "type": "error", "payload": view.model_dump(mode="json", by_alias=True)})
            if self.store:
                row = self.store.get(str(pull_id))
                if row:
                    row.state, row.status, row.cancel_requested, row.error_code, row.updated_at = view.state, view.status, view.cancel_requested, view.error_code, view.updated_at
                    self.store.save(row)
        return view
