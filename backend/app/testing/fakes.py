from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

from app.api.errors import DomainError
from app.config import EmbeddingSettings
from app.core.embedding import FakeEmbeddingProvider
from app.core.llm import ChatDelta, ChatRequest, ModelConnectionResult
from app.yuque.gateway import FakeYuqueGateway


class E2EControl:
    """One-shot fake-service controls written by the Electron E2E harness."""

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "e2e" / "control"

    def consume(self, command: str) -> bool:
        claimed = self.path.with_name(f"{self.path.name}.{uuid4().hex}.claimed")
        try:
            self.path.replace(claimed)
        except FileNotFoundError:
            return False
        try:
            if claimed.read_text(encoding="utf-8").strip() == command:
                return True
            claimed.replace(self.path)
            return False
        finally:
            claimed.unlink(missing_ok=True)


class FakeLLMProvider:
    """A local, deterministic LLM substitute for fake-services workflows."""

    def __init__(
        self, delay_seconds: float = 0.01, control: E2EControl | None = None
    ) -> None:
        self.delay_seconds = delay_seconds
        self.control = control

    async def test_connection(self) -> ModelConnectionResult:
        if self.control is not None and self.control.consume("fail-next-model-test"):
            raise DomainError(
                "MODEL_AUTH_FAILED",
                "模型服务认证失败，请检查 API Key",
                401,
                False,
                "检查 API Key 后重试",
            )
        return ModelConnectionResult(connected=True, latency_ms=0)

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatDelta]:
        context = _context_from_request(request)
        if not context:
            return
        answer = f"{_context_words(context)} [S1]"
        for part in _three_parts(answer):
            if self.delay_seconds:
                await asyncio.sleep(self.delay_seconds)
            yield ChatDelta(content=part)


class E2EControlledFakeEmbeddingProvider(FakeEmbeddingProvider):
    """Fake embedding provider with deterministic E2E-only import controls."""

    def __init__(
        self,
        settings: EmbeddingSettings,
        *,
        control: E2EControl,
        delay_seconds: float = 1.0,
    ) -> None:
        super().__init__(settings)
        self.control = control
        self.delay_seconds = delay_seconds

    async def ensure_ready(self):  # type: ignore[no-untyped-def]
        return await super().ensure_ready()

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self.control.consume("delay-next-import"):
            await asyncio.sleep(self.delay_seconds)
        if self.control.consume("fail-next-index"):
            raise DomainError("INDEX_FAILED", "嵌入模型不可用", 503, True)
        return await super().embed_documents(texts)


def _context_from_request(request: ChatRequest) -> str:
    for message in reversed(request.messages):
        if "[S1]" not in message.content or "文档片段：" not in message.content:
            continue
        match = re.search(r"内容：(.*?)(?:\n\n问题：|$)", message.content, re.DOTALL)
        if match and match.group(1).strip():
            return match.group(1).strip()
    return ""


def _context_words(context: str) -> str:
    words = re.findall(r"@?[A-Za-z0-9_]+|[\u4e00-\u9fff]+", context)
    selected = " ".join(words[:8]).strip()
    return selected or "当前文档未覆盖"


def _three_parts(text: str) -> tuple[str, str, str]:
    first = max(1, len(text) // 3)
    second = max(first + 1, (len(text) * 2) // 3)
    return text[:first], text[first:second], text[second:]


__all__ = [
    "E2EControl",
    "E2EControlledFakeEmbeddingProvider",
    "FakeLLMProvider",
    "FakeYuqueGateway",
]
