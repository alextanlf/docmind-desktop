"""Local inference server status and model listing.

DocMind does not download models. Users who run one locally already installed it
with whatever tool runs it, so the only two questions worth answering here are
"is your server up" and "what can I ask it".
"""

from fastapi import APIRouter, Request

from app.core.local_model_service import LocalModelService
from app.schemas.common import WireModel
from app.schemas.local_model import LocalModelsView, LocalStatusView

router = APIRouter(prefix="/api/local-model", tags=["local-model"])


class LocalModelsProbe(WireModel):
    """Optional filters so a UI can list models without re-sending credentials."""

    model: str = ""


def _service(request: Request) -> LocalModelService:
    service = getattr(request.app.state, "local_model_service", None)
    if service is None:
        service = LocalModelService()
        request.app.state.local_model_service = service
    return service


@router.get("/status", response_model=LocalStatusView)
async def status(request: Request):
    return await _service(request).status()


@router.get("/models", response_model=LocalModelsView)
async def models(request: Request):
    return await _service(request).models()
