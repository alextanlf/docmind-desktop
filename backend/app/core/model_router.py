from __future__ import annotations
from dataclasses import dataclass
from collections.abc import AsyncIterator
from app.api.errors import DomainError
from app.core.llm import ChatDelta, ChatRequest, LLMProvider
from app.schemas.ollama import GenerationRoute

FALLBACK_CODES = {"OLLAMA_UNAVAILABLE", "OLLAMA_MODEL_NOT_INSTALLED", "LOCAL_MODEL_UNAVAILABLE"}
@dataclass
class RoutedStream:
    route: GenerationRoute
    deltas: AsyncIterator[ChatDelta]

class ModelRouter:
    def __init__(self, mode, local: LLMProvider, cloud: LLMProvider, local_model: str, cloud_model: str):
        self.mode, self.local, self.cloud, self.local_model, self.cloud_model = mode, local, cloud, local_model, cloud_model

    async def open_stream(self, request: ChatRequest) -> RoutedStream:
        if self.mode == "cloud_only":
            return RoutedStream(GenerationRoute(source="cloud", model=self.cloud_model, mode=self.mode), self.cloud.stream_chat(request))
        try:
            stream = self.local.stream_chat(request)
            first = None
            while first is None or not first.content:
                try:
                    candidate = await stream.__anext__()
                except StopAsyncIteration as error:
                    raise DomainError("LOCAL_MODEL_UNAVAILABLE", "本地模型未返回内容", 503, True) from error
                if candidate.content:
                    first = candidate
        except Exception as error:
            if self.mode == "automatic" and getattr(error, "code", None) in FALLBACK_CODES:
                return RoutedStream(GenerationRoute(source="cloud", model=self.cloud_model, mode=self.mode, fallback_reason=error.code), self.cloud.stream_chat(request))
            raise
        async def with_first():
            yield first
            async for delta in stream: yield delta
        return RoutedStream(GenerationRoute(source="local", model=self.local_model, mode=self.mode), with_first())
