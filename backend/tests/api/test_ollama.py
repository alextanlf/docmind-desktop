import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.config import AppSettings
from app.main import create_app


@pytest.mark.asyncio
async def test_ollama_status_endpoint_returns_safe_snapshot(tmp_path):
    settings = AppSettings(environment="test", data_dir=tmp_path, session_token=SecretStr("test-runtime-token"))
    app = create_app(settings=settings)
    app.state.ollama_service = None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-DocMind-Token": "test-runtime-token"}) as client:
        response = await client.get("/api/ollama/status")
    assert response.status_code in {200, 503}
