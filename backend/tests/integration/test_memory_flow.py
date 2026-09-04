def test_memory_services_are_wired(client):
    assert client.app.state.memory_indexer is not None
    assert client.app.state.memory_retriever is not None
    assert client.app.state.chat_service.memory_retriever is client.app.state.memory_retriever


def test_memory_citation_contract_is_discriminated():
    from app.chat.service import _memory_source_map
    from app.memory.retriever import MemoryHit

    citation = _memory_source_map([MemoryHit("v", "distillation", "d", "x", "r", 1.0, {"session_id": "s"})])["M1"]
    assert citation.kind == "memory"
    assert citation.memory_kind == "distillation"
    assert citation.memory_id == "d"
    assert citation.session_id == "s"
