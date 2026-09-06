import httpx
import pytest
from app.storage.database import Database
from app.storage.repositories import OllamaPullStore

from app.core.ollama_service import OllamaService


@pytest.mark.asyncio
async def test_models_normalize_tags_response():
    def handler(request):
        return httpx.Response(200, json={"models": [{"name": "qwen2.5:7b", "size": 12, "digest": "sha256:x"}]})
    service = OllamaService("http://127.0.0.1:11434", transport=httpx.MockTransport(handler))
    result = await service.models()
    assert result.available is True
    assert result.models[0].name == "qwen2.5:7b"

@pytest.mark.asyncio
async def test_unavailable_models_are_safe_snapshot():
    def handler(request):
        raise httpx.ConnectError("offline")
    result = await OllamaService("http://127.0.0.1:11434", transport=httpx.MockTransport(handler)).models()
    assert result.available is False and result.models == []

def test_pull_survives_service_recreation(tmp_path):
    db = Database(f"sqlite+pysqlite:///{tmp_path/'db.sqlite'}"); db.upgrade(); store = OllamaPullStore(db)
    first = OllamaService("http://127.0.0.1:11434", store=store).create_pull("m")
    second = OllamaService("http://127.0.0.1:11434", store=store)
    assert second.get_pull(first.id).model_name == "m"

def test_duplicate_active_pull_is_reused(tmp_path):
    db = Database(f"sqlite+pysqlite:///{tmp_path/'db.sqlite'}"); db.upgrade(); store = OllamaPullStore(db)
    service = OllamaService("http://127.0.0.1:11434", store=store)
    first = service.create_pull("m"); second = service.create_pull("m")
    assert first.id == second.id
