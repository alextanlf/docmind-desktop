def test_local_knowledge_path_is_logical_and_atomic(tmp_path):
    from app.memory.persistence import LocalKnowledgeStore
    path, digest = LocalKnowledgeStore(tmp_path).save("id", "title", "content")
    assert path.startswith("knowledge/") and not path.startswith("/") and digest
    assert not list((tmp_path / "knowledge").glob("*.partial"))

