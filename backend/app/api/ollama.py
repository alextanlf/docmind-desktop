import json
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import field_validator

from app.api.errors import DomainError
from app.core.ollama_service import OllamaService
from app.schemas.common import WireModel
from app.schemas.ollama import OllamaModelsView, OllamaStatusView, validate_model_tag

router = APIRouter(prefix="/api/ollama", tags=["ollama"])


class PullInput(WireModel):
    model_name: str
    @field_validator("model_name")
    @classmethod
    def valid_model_name(cls, value: str) -> str:
        return validate_model_tag(value)



def _service(request: Request) -> OllamaService:
    service = getattr(request.app.state, "ollama_service", None)
    if service is None:
        service = OllamaService("http://127.0.0.1:11434")
        request.app.state.ollama_service = service
    return service

@router.get("/status", response_model=OllamaStatusView)
async def status(request: Request):
    return await _service(request).status()

@router.get("/models", response_model=OllamaModelsView)
async def models(request: Request):
    return await _service(request).models()

@router.post("/models/pull")
async def create_pull(input: PullInput, request: Request):
    service = _service(request)
    snapshot = service.create_pull(input.model_name)
    return snapshot

@router.get("/models/pull/{pull_id}")
async def get_pull(pull_id: UUID, request: Request):
    return _service(request).get_pull(pull_id)

@router.post("/models/pull/{pull_id}/cancel")
async def cancel_pull(pull_id: UUID, request: Request):
    return _service(request).cancel_pull(pull_id)

@router.post("/models/pull/{pull_id}/retry")
async def retry_pull(pull_id: UUID, request: Request):
    service = _service(request)
    snapshot = service.retry_pull(pull_id)
    return snapshot

@router.get("/models/pull/{pull_id}/events")
async def pull_events(pull_id: UUID, request: Request):
    service = _service(request)
    header = request.headers.get("last-event-id", "0")
    try:
        after = int(header)
    except (TypeError, ValueError):
        raise DomainError("INVALID_REQUEST", "请求参数无效", 422) from None
    if after < 0:
        raise DomainError("INVALID_REQUEST", "请求参数无效", 422)
    snapshot = service.get_pull(pull_id)

    async def stream():
        events = service._events.get(pull_id, [])
        sent = False
        for event in events:
            sequence = int(event["sequence"])
            if sequence <= after:
                continue
            event_type = str(event.get("type") or "progress")
            payload = dict(event.get("payload") or {})
            if event_type == "error":
                payload.setdefault("code", snapshot.error_code or "OLLAMA_PULL_FAILED")
                payload.setdefault("message", snapshot.error_message or "模型拉取失败")
                payload.setdefault("retryable", snapshot.retryable)
            envelope = {
                "requestId": str(pull_id),
                "type": event_type,
                "sequence": sequence,
                "payload": payload,
            }
            yield (
                f"id: {sequence}\nevent: {event_type}\ndata: "
                f"{json.dumps(envelope, ensure_ascii=False, separators=(',', ':'))}\n\n"
            )
            sent = True
        # A terminal snapshot is authoritative even if the requested cursor
        # is newer than the in-memory replay buffer.
        if not sent and snapshot.state in {"completed", "failed", "cancelled"}:
            event_type = "done" if snapshot.state == "completed" else "error"
            payload = snapshot.model_dump(mode="json", by_alias=True)
            if event_type == "error":
                payload.update(
                    {
                        "code": snapshot.error_code or "OLLAMA_PULL_FAILED",
                        "message": snapshot.error_message or "模型拉取失败",
                        "retryable": snapshot.retryable,
                    }
                )
            sequence = snapshot.last_event_sequence
            envelope = {
                "requestId": str(pull_id),
                "type": event_type,
                "sequence": sequence,
                "payload": payload,
            }
            yield (
                f"id: {sequence}\nevent: {event_type}\ndata: "
                f"{json.dumps(envelope, ensure_ascii=False, separators=(',', ':'))}\n\n"
            )
        elif not sent:
            payload = snapshot.model_dump(mode="json", by_alias=True)
            sequence = snapshot.last_event_sequence
            envelope = {
                "requestId": str(pull_id),
                "type": "progress",
                "sequence": sequence,
                "payload": payload,
            }
            yield (
                f"id: {sequence}\nevent: progress\ndata: "
                f"{json.dumps(envelope, ensure_ascii=False, separators=(',', ':'))}\n\n"
            )
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control":"no-cache"})
