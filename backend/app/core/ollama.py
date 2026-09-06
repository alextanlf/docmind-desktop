from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from app.api.errors import DomainError
from app.core.llm import ChatDelta, ChatRequest, ModelConnectionResult


class OllamaProvider:
    def __init__(self, base_url: str, model: str, timeout: float = 120, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url, self.model, self.timeout, self.transport = base_url.rstrip("/"), model, timeout, transport

    async def test_connection(self) -> ModelConnectionResult:
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
        except httpx.HTTPError as error:
            raise DomainError("OLLAMA_UNAVAILABLE", "Ollama 未运行或暂时无法连接", 503, True) from error
        return ModelConnectionResult(connected=True, latency_ms=0)

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatDelta]:
        payload = {"model": self.model, "messages": [m.model_dump() for m in request.messages], "stream": True, "options": {"temperature": request.temperature}}
        emitted = False
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                async with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response:
                    if response.status_code >= 400:
                        raise DomainError("OLLAMA_UNAVAILABLE", "Ollama 未运行或暂时无法连接", 503, True)
                    async for line in response.aiter_lines():
                        if len(line.encode()) > 1024 * 1024:
                            raise DomainError("OLLAMA_PROTOCOL_ERROR", "Ollama 返回了无法识别的数据", 502)
                        try:
                            item = json.loads(line)
                        except json.JSONDecodeError as error:
                            raise DomainError("OLLAMA_PROTOCOL_ERROR", "Ollama 返回了无法识别的数据", 502) from error
                        if item.get("error"):
                            raise DomainError("OLLAMA_PROTOCOL_ERROR", "Ollama 返回了无法识别的数据", 502)
                        content = item.get("message", {}).get("content")
                        if content:
                            emitted = True
                            yield ChatDelta(content=content)
                        if item.get("done"):
                            if not emitted:
                                raise DomainError("OLLAMA_PROTOCOL_ERROR", "Ollama 返回了无法识别的数据", 502)
                            return
        except httpx.TimeoutException as error:
            raise DomainError("OLLAMA_UNAVAILABLE", "Ollama 未运行或暂时无法连接", 503, True) from error
        if not emitted:
            raise DomainError("OLLAMA_PROTOCOL_ERROR", "Ollama 返回了无法识别的数据", 502)
        raise DomainError("OLLAMA_PROTOCOL_ERROR", "Ollama 返回了无法识别的数据", 502)
