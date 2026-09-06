from fastapi import APIRouter, Request
from app.core.ollama_service import OllamaService
from app.schemas.ollama import OllamaModelsView, OllamaStatusView

router = APIRouter(prefix="/api/ollama", tags=["ollama"])

def _service(request: Request) -> OllamaService:
    service = getattr(request.app.state, "ollama_service", None)
    if service is None:
        service = OllamaService("http://127.0.0.1:11434")
    return service

@router.get("/status", response_model=OllamaStatusView)
async def status(request: Request):
    return await _service(request).status()

@router.get("/models", response_model=OllamaModelsView)
async def models(request: Request):
    return await _service(request).models()
