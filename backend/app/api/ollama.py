from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
import json
from pydantic import BaseModel
from uuid import UUID
from app.core.ollama_service import OllamaService
from app.schemas.ollama import OllamaModelsView, OllamaStatusView
from app.schemas.common import WireModel

router = APIRouter(prefix="/api/ollama", tags=["ollama"])
class PullInput(WireModel):
    model_name: str

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
    return _service(request).create_pull(input.model_name)

@router.get("/models/pull/{pull_id}")
async def get_pull(pull_id: UUID, request: Request):
    return _service(request).get_pull(pull_id)

@router.post("/models/pull/{pull_id}/cancel")
async def cancel_pull(pull_id: UUID, request: Request):
    return _service(request).cancel_pull(pull_id)

@router.post("/models/pull/{pull_id}/retry")
async def retry_pull(pull_id: UUID, request: Request):
    return _service(request).retry_pull(pull_id)

@router.get("/models/pull/{pull_id}/events")
async def pull_events(pull_id: UUID, request: Request):
    snapshot = _service(request).get_pull(pull_id)
    try: after = max(0, int(request.headers.get("last-event-id", "0")))
    except ValueError: after = 0
    async def stream():
        events = _service(request)._events.get(pull_id, [])
        for event in events:
            if int(event["sequence"]) <= after: continue
            envelope = {"requestId": str(pull_id), "type": "progress", "sequence": event["sequence"], "payload": event["payload"]}
            yield f"id: {event['sequence']}\nevent: progress\ndata: {json.dumps(envelope, ensure_ascii=False)}\n\n"
        if not events:
            envelope = {"requestId": str(pull_id), "type": "progress", "sequence": snapshot.last_event_sequence, "payload": snapshot.model_dump(mode="json", by_alias=True)}
            yield f"id: {snapshot.last_event_sequence}\nevent: progress\ndata: {json.dumps(envelope, ensure_ascii=False)}\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control":"no-cache"})
