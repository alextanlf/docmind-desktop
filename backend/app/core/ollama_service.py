from __future__ import annotations
from datetime import UTC, datetime
import httpx
from app.schemas.ollama import OllamaModelView, OllamaModelsView

class OllamaService:
    def __init__(self, base_url: str, model: str = "", transport: httpx.AsyncBaseTransport | None = None, timeout: float = 5) -> None:
        self.base_url, self.model, self.transport, self.timeout = base_url.rstrip("/"), model, transport, timeout

    async def models(self) -> OllamaModelsView:
        checked = datetime.now(UTC)
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                payload = response.json()
            models = [OllamaModelView(name=item["name"], digest=item.get("digest"), size_bytes=item.get("size"), modified_at=item.get("modified_at"), family=item.get("details", {}).get("family")) for item in payload.get("models", []) if isinstance(item, dict) and isinstance(item.get("name"), str)]
            return OllamaModelsView(available=True, models=models, checked_at=checked, message="Ollama 已连接")
        except Exception:
            return OllamaModelsView(available=False, models=[], checked_at=checked, message="Ollama 未运行或暂时无法连接")

    async def status(self):
        models = await self.models()
        names = {item.name for item in models.models}
        from app.schemas.ollama import OllamaStatusView
        return OllamaStatusView(available=models.available, base_url=self.base_url, selected_model=self.model, selected_model_installed=self.model in names, checked_at=models.checked_at, message=models.message)
