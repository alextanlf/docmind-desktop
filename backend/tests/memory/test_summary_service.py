import pytest

from app.memory.summary import SummaryService
from app.storage.repositories import ConversationStore, MemoryStore


class LLM:
    async def stream_chat(self, request):
        from app.core.llm import ChatDelta
        yield ChatDelta(content="# Topic\nsummary")


@pytest.mark.asyncio
async def test_end_session_generates_that_exact_session(database):
    conversations = ConversationStore(database)
    first = conversations.create_session([])
    second = conversations.create_session([])
    service = SummaryService(conversations, MemoryStore(database), llm=LLM())
    await service.end_session(second.id)
    assert MemoryStore(database).get_summary(second.id).state == "ready"
    assert MemoryStore(database).get_summary(first.id) is None

