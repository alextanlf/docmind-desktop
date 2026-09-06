import pytest

from app.memory.distillation import DistillationService
from app.storage.repositories import ConversationStore


class LLM:
    async def stream_chat(self, request):
        from app.core.llm import ChatDelta
        yield ChatDelta(content="# Draft\n- point")


@pytest.mark.asyncio
async def test_distillation_produces_editable_draft_and_key_points(database):
    store = ConversationStore(database)
    session = store.create_session(["repo-a"])
    draft = await DistillationService(store, LLM()).create(session.id)
    assert draft.state == "draft"
    assert "point" in draft.key_points_json

