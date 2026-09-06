from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import asyncio
import socket

from app.api.errors import DomainError
from app.core.llm import ChatDelta, ChatRequest, ModelConnectionResult
from app.core.ollama_validation import normalize_loopback_base_url

class _SystemResolver:
    async def resolve(self, host: str):
        rows = await asyncio.to_thread(socket.getaddrinfo, host, 11434, type=socket.SOCK_STREAM)
        return sorted({row[4][0] for row in rows})


class OllamaProvider:
    def __init__(self, base_url: str, model: str, timeout: float = 120, transport: httpx.AsyncBaseTransport | None = None, resolver=None) -> None:
        self.base_url, self.model, self.timeout, self.transport, self.resolver = base_url.rstrip("/"), model, timeout, transport, resolver or _SystemResolver()

    async def test_connection(self) -> ModelConnectionResult:
        try:
            self.base_url = await normalize_loopback_base_url(self.base_url, self.resolver)
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
            self.base_url = await normalize_loopback_base_url(self.base_url, self.resolver)
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


class PullCoordinator:
    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 600, resolver=None) -> None:
        self.base_url, self.transport, self.timeout, self.resolver = base_url.rstrip("/"), transport, timeout, resolver or _SystemResolver()

    async def pull(self, model_name: str) -> AsyncIterator[dict[str, object]]:
        try:
            self.base_url = await normalize_loopback_base_url(self.base_url, self.resolver)
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                async with client.stream("POST", f"{self.base_url}/api/pull", json={"name": model_name, "stream": True}) as response:
                    if response.status_code >= 400:
                        raise DomainError("OLLAMA_PULL_FAILED", "模型拉取失败", 502, True)
                    async for line in response.aiter_lines():
                        try: item = json.loads(line)
                        except json.JSONDecodeError as error: raise DomainError("OLLAMA_PROTOCOL_ERROR", "Ollama 返回了无法识别的数据", 502) from error
                        if item.get("error"): raise DomainError("OLLAMA_PULL_FAILED", "模型拉取失败", 502, True)
                        total, completed = item.get("total"), item.get("completed")
                        event: dict[str, object] = {"status": str(item.get("status", "正在下载"))}
                        if isinstance(total, (int, float)) and isinstance(completed, (int, float)) and total > 0:
                            event["progress"] = max(0, min(100, round(completed * 100 / total)))
                        if item.get("done"): event["state"] = "completed"
                        yield event
        except httpx.HTTPError as error:
            raise DomainError("OLLAMA_PULL_FAILED", "模型拉取失败", 502, True) from error
