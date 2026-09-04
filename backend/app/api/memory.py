from uuid import UUID

from fastapi import APIRouter, Request

from app.schemas.memory import DistillationEdit, DistillationTarget

router = APIRouter(prefix="/api")


@router.get("/sessions/{session_id}/summary")
def get_summary(session_id: UUID, request: Request):
    return request.app.state.memory_store.get_summary(str(session_id))


@router.post("/sessions/{session_id}/summary/regenerate")
async def regenerate_summary(session_id: UUID, request: Request):
    return await request.app.state.summary_service.regenerate(str(session_id))


@router.post("/sessions/{session_id}/distillations")
async def create_distillation(session_id: UUID, request: Request):
    return await request.app.state.distillation_service.create(str(session_id))


@router.get("/distillations/{distillation_id}")
def get_distillation(distillation_id: UUID, request: Request):
    return request.app.state.distillation_service.get(str(distillation_id))


@router.put("/distillations/{distillation_id}")
def update_distillation(distillation_id: UUID, body: DistillationEdit, request: Request):
    return request.app.state.distillation_service.update(str(distillation_id), body)


@router.post("/distillations/{distillation_id}/regenerate")
async def regenerate_distillation(distillation_id: UUID, request: Request):
    return await request.app.state.distillation_service.regenerate(str(distillation_id))


@router.post("/distillations/{distillation_id}/save")
async def save_distillation(distillation_id: UUID, body: DistillationTarget, request: Request):
    return await request.app.state.distillation_service.save(str(distillation_id), body)

@router.delete("/distillations/{distillation_id}", status_code=204)
def delete_distillation(distillation_id: UUID, request: Request):
    with request.app.state.database.session() as session:
        from app.storage.models import DistillationRecord
        record = session.get(DistillationRecord, str(distillation_id))
        if record is None:
            return
        session.delete(record)

@router.get("/memories")
def list_memories(request: Request, repositoryIds: str, kind: str | None = None):
    ids = [item for item in repositoryIds.split(",") if item]
    if not ids:
        return {"items": [], "nextCursor": None}
    return request.app.state.memory_retriever.search("", ids, 0) if hasattr(request.app.state, "memory_retriever") else {"items": [], "nextCursor": None}
