from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from app.api.errors import DomainError
from app.core.llm import ChatDelta, ChatRequest, LLMProvider
from app.schemas.ollama import GenerationRoute, RuntimeSettingsInput

FALLBACK_CODES = frozenset(
    {"OLLAMA_UNAVAILABLE", "OLLAMA_MODEL_NOT_INSTALLED", "LOCAL_MODEL_UNAVAILABLE"}
)


@dataclass(frozen=True)
class RoutedStream:
    route: GenerationRoute
    deltas: AsyncIterator[ChatDelta]


class ModelRouter:
    """Select local/cloud generation while enforcing the first-delta boundary."""

    def __init__(
        self,
        mode: str | None = None,
        local: LLMProvider | None = None,
        cloud: LLMProvider | None = None,
        local_model: str = "",
        cloud_model: str = "",
        *,
        runtime: RuntimeSettingsInput | Any | None = None,
        runtime_reader: Callable[[], Any] | None = None,
        local_preflight: Callable[..., Any] | None = None,
        local_service: Any | None = None,
    ) -> None:
        if local is None or cloud is None:
            raise TypeError("local and cloud providers are required")
        self.local = local
        self.cloud = cloud
        self.local_model = local_model
        self.cloud_model = cloud_model or self._provider_model(cloud) or "cloud"
        self._static_mode = mode
        self._runtime = runtime
        self._runtime_reader = runtime_reader
        self._local_preflight = local_preflight
        self._local_service = local_service

    @staticmethod
    def _provider_model(provider: Any) -> str:
        config = getattr(provider, "config", None)
        model = getattr(config, "model", None)
        return model if isinstance(model, str) else ""

    async def _read_runtime(self) -> tuple[str, str]:
        value: Any = self._runtime_reader() if self._runtime_reader is not None else self._runtime
        if inspect.isawaitable(value):
            value = await value
        if value is None:
            return self._static_mode or "cloud_only", self.local_model
        routing = getattr(value, "routing", value)
        mode = getattr(routing, "mode", None) or getattr(value, "mode", None) or self._static_mode or "cloud_only"
        ollama = getattr(value, "ollama", None)
        model = getattr(ollama, "model", None) if ollama is not None else None
        return str(mode), model if isinstance(model, str) else self.local_model

    async def _call_preflight(self, model: str) -> None:
        if not model.strip():
            raise DomainError("LOCAL_MODEL_UNAVAILABLE", "未配置本地模型", 503, False)

        if self._local_preflight is not None:
            callback = self._local_preflight
            try:
                parameters = inspect.signature(callback).parameters
            except (TypeError, ValueError):
                parameters = {}
            if len(parameters) == 0:
                result = callback()
            elif len(parameters) == 1:
                result = callback(model)
            else:
                result = callback(model, self.local)
            if inspect.isawaitable(result):
                result = await result
            if result is False:
                raise DomainError("OLLAMA_MODEL_NOT_INSTALLED", "选定模型尚未安装", 503, True)
            return

        if self._local_service is not None:
            preflight = getattr(self._local_service, "preflight_model", None)
            if preflight is not None:
                result = preflight(model)
                if inspect.isawaitable(result):
                    await result
                return
            checker = getattr(self._local_service, "is_model_installed", None)
            if checker is not None:
                result = checker(model)
                if inspect.isawaitable(result):
                    result = await result
                if result is False:
                    raise DomainError("OLLAMA_MODEL_NOT_INSTALLED", "选定模型尚未安装", 503, True)

        checker = getattr(self.local, "test_connection", None)
        if checker is not None:
            result = checker()
            if inspect.isawaitable(result):
                result = await result
            if result is not None and getattr(result, "connected", True) is False:
                raise DomainError("OLLAMA_UNAVAILABLE", "Ollama 未运行或暂时无法连接", 503, True)

        checker = getattr(self.local, "is_model_installed", None)
        if checker is not None:
            result = checker(model)
            if inspect.isawaitable(result):
                result = await result
            if result is False:
                raise DomainError("OLLAMA_MODEL_NOT_INSTALLED", "选定模型尚未安装", 503, True)

    @staticmethod
    async def _first_non_empty(stream: AsyncIterator[ChatDelta]) -> tuple[ChatDelta, AsyncIterator[ChatDelta]]:
        try:
            while True:
                delta = await stream.__anext__()
                if not isinstance(delta, ChatDelta):
                    raise DomainError("OLLAMA_PROTOCOL_ERROR", "Ollama 返回了无法识别的数据", 502)
                if delta.content:
                    return delta, stream
        except StopAsyncIteration as error:
            raise DomainError("LOCAL_MODEL_UNAVAILABLE", "本地模型未返回内容", 503, True) from error

    @staticmethod
    async def _prepend(first: ChatDelta, stream: AsyncIterator[ChatDelta]) -> AsyncIterator[ChatDelta]:
        yield first
        async for delta in stream:
            yield delta

    async def _open_fallback(self, request: ChatRequest, mode: str, reason: str) -> RoutedStream:
        route = GenerationRoute(
            source="cloud",
            model=self.cloud_model,
            mode=mode,  # type: ignore[arg-type]
            fallback_reason=reason,
        )
        try:
            stream = self.cloud.stream_chat(request)
            first, stream = await self._first_non_empty(stream)
        except Exception as error:
            retryable = getattr(error, "retryable", True)
            raise DomainError(
                "ROUTING_CLOUD_UNAVAILABLE", "本地和云端都不可用", 503, retryable
            ) from error
        return RoutedStream(route, self._prepend(first, stream))
    async def open_stream(self, request: ChatRequest) -> RoutedStream:
        mode, local_model = await self._read_runtime()
        if mode not in {"local_only", "cloud_only", "automatic"}:
            mode = "cloud_only"

        if mode == "cloud_only":
            route = GenerationRoute(source="cloud", model=self.cloud_model, mode=mode)
            return RoutedStream(route, self.cloud.stream_chat(request))

        try:
            await self._call_preflight(local_model)
            stream = self.local.stream_chat(request)
            first, stream = await self._first_non_empty(stream)
        except Exception as error:
            code = getattr(error, "code", "LOCAL_MODEL_UNAVAILABLE")
            if mode == "automatic" and code in FALLBACK_CODES:
                return await self._open_fallback(request, mode, code)
            if mode == "local_only":
                raise DomainError(
                    "LOCAL_MODEL_UNAVAILABLE", "本地模型不可用", 503, getattr(error, "retryable", True)
                ) from error
            raise

        route = GenerationRoute(source="local", model=local_model, mode=mode)
        # Once the first non-empty delta is obtained, all subsequent failures
        # are intentionally left in the local stream; no cloud retry occurs.
        return RoutedStream(route, self._prepend(first, stream))
