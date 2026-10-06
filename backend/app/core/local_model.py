"""Local inference over the OpenAI-compatible dialect.

Every local server worth supporting (Ollama, LM Studio, llama.cpp's server,
vLLM, Jan) exposes `/v1/chat/completions` and `/v1/models`. Ollama also keeps
its own `/api/chat` NDJSON dialect, but supporting it would mean maintaining a
second response parser for no gain, so this module talks the common dialect and
delegates the wire format to `OpenAICompatibleProvider`.

What stays local-specific:
  * the loopback-only URL guard (a security boundary, not a vendor rule);
  * error codes, so the UI can still distinguish "server is not running" from
    "that model id is not available here";
  * the empty `data: null` shape every local server emits when it has no models
    loaded — the cloud path treats that as a protocol error, which would be a
    misleading thing to show someone who simply has not loaded a model yet.
"""

from __future__ import annotations

import asyncio
import socket
import time
from collections.abc import AsyncIterator

import httpx

from app.api.errors import DomainError
from app.core.llm import (
    ChatDelta,
    ChatRequest,
    ModelConfig,
    ModelConnectionResult,
    OpenAICompatibleProvider,
)
from app.core.local_model_validation import (
    UNAVAILABLE_CODE,
    UNAVAILABLE_MESSAGE,
    normalize_loopback_base_url,
)
from app.schemas.local_model import DEFAULT_LOCAL_BASE_URL, LocalModelConfig, validate_model_tag

# The capability table is keyed by (preset, model) for cloud vendors. Local ids
# are arbitrary user-chosen strings, so a dedicated preset routes them to the
# conservative profile: temperature is accepted, no reasoning field is sent.
LOCAL_PRESET = "local"

MODEL_NOT_FOUND_CODE = "LOCAL_MODEL_NOT_FOUND"
MODEL_NOT_FOUND_MESSAGE = "该本地模型不可用，请检查模型名称"
PROTOCOL_CODE = "LOCAL_MODEL_PROTOCOL_ERROR"
PROTOCOL_MESSAGE = "本地模型服务返回了无法识别的数据"
CHAT_DEADLINE_SECONDS = 10 * 60
_MAX_LINE_BYTES = 1 << 20


class _SystemResolver:
    """Resolve against the port the caller is actually going to use.

    The port is part of the resolution request, so rebinding tests that vary it
    exercise the same path production does.
    """

    def __init__(self, port: int | None = None) -> None:
        self._port = port

    async def resolve(self, host: str):
        rows = await asyncio.to_thread(
            socket.getaddrinfo, host, self._port, type=socket.SOCK_STREAM
        )
        return sorted({row[4][0] for row in rows})


def _unavailable_error(cause: BaseException | None = None) -> DomainError:
    error = DomainError(UNAVAILABLE_CODE, UNAVAILABLE_MESSAGE, 503, True)
    if cause is not None:
        error.__cause__ = cause
    return error


def _protocol_error() -> DomainError:
    return DomainError(PROTOCOL_CODE, PROTOCOL_MESSAGE, 502, False)


def _http_error(status_code: int) -> DomainError:
    if status_code == 404:
        return DomainError(MODEL_NOT_FOUND_CODE, MODEL_NOT_FOUND_MESSAGE, 503, True)
    if status_code >= 500:
        return _unavailable_error()
    return _protocol_error()


class LocalModelProvider:
    """Chat against a local OpenAI-compatible server."""

    def __init__(
        self,
        config_or_base_url: LocalModelConfig | str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver=None,
        *,
        api_key: str | None = None,
        config: LocalModelConfig | None = None,
    ) -> None:
        if config is None and isinstance(config_or_base_url, LocalModelConfig):
            config = config_or_base_url
            config_or_base_url = None
        if config is None:
            config = LocalModelConfig(
                base_url=config_or_base_url or DEFAULT_LOCAL_BASE_URL,
                model=model or "",
                api_key=api_key or "",
                timeout_seconds=120.0 if timeout is None else timeout,
            )
        self.config = config
        if self.config.model:
            validate_model_tag(self.config.model)
        self.transport = transport
        self.resolver = resolver
        self._base_url: str | None = None

    async def _base(self) -> str:
        if self._base_url is not None:
            return self._base_url
        base = self.config.base_url
        if self.resolver is not None:
            base = await normalize_loopback_base_url(base, self.resolver)
        self._base_url = base
        return base

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.config.timeout_seconds, transport=self.transport)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def _delegate(self, base: str) -> OpenAICompatibleProvider:
        """Reuse the cloud implementation for wire-format work.

        Only the URL and the capability-table preset differ; the payload
        construction, SSE parsing and delta extraction are identical, which is
        precisely why a second parser would have been waste.
        """
        return OpenAICompatibleProvider(
            ModelConfig(
                preset=LOCAL_PRESET,
                base_url=f"{base}/v1",
                model=self.config.model,
                timeout_seconds=self.config.timeout_seconds,
            ),
            self.config.api_key,
        )

    async def list_models(self) -> list[str]:
        """Model ids this server currently offers.

        An empty list is a normal state (nothing loaded yet), not an error.
        """
        base = await self._base()
        url = f"{base}/v1/models"
        try:
            async with self._client() as client:
                response = await client.get(url, headers=self._headers())
        except (httpx.TimeoutException, httpx.HTTPError) as error:
            raise _unavailable_error(error) from error
        if response.status_code in (404, 405):
            return []
        if response.status_code >= 400:
            raise _http_error(response.status_code)
        try:
            payload = response.json()
        except ValueError as error:
            raise _protocol_error() from error
        if not isinstance(payload, dict):
            raise _protocol_error()
        entries = payload.get("data")
        # Every local server answers `data: null` when nothing is loaded, and
        # some answer `data: []`. Both mean "no models", which is not a
        # protocol violation — treating them as one produced a scary
        # "unrecognised data" error on every fresh install.
        if entries is None:
            return []
        if not isinstance(entries, list):
            raise _protocol_error()
        models: list[str] = []
        for entry in entries:
            model_id = entry.get("id") if isinstance(entry, dict) else None
            if not isinstance(model_id, str) or not model_id.strip():
                continue
            models.append(model_id.strip())
        return models

    async def test_connection(self) -> ModelConnectionResult:
        started = time.perf_counter()
        try:
            base = await self._base()
            async with self._client() as client:
                response = await client.get(f"{base}/v1/models", headers=self._headers())
        except (httpx.TimeoutException, httpx.HTTPError) as error:
            raise _unavailable_error(error) from error
        if response.status_code >= 400:
            raise _http_error(response.status_code)
        return ModelConnectionResult(
            connected=True,
            latency_ms=round((time.perf_counter() - started) * 1000),
        )

    async def preflight_model(self, model_name: str) -> None:
        """Fail before a chat request when the model is not available here.

        Distinct from `test_connection`: a running server with the wrong model
        selected is a configuration problem, not an outage, and the UI needs to
        say so.
        """
        model_name = validate_model_tag(model_name)
        available = await self.list_models()
        if model_name not in available:
            raise DomainError(MODEL_NOT_FOUND_CODE, MODEL_NOT_FOUND_MESSAGE, 503, True)

    async def is_model_installed(self, model_name: str | None = None) -> bool:
        selected = model_name if model_name is not None else self.config.model
        if not selected:
            return False
        return selected in await self.list_models()

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatDelta]:
        base = await self._base()
        provider = self._delegate(base)
        try:
            async for delta in provider.stream_chat(request):
                yield delta
        except DomainError as error:
            raise _translate(error) from error
        except httpx.TimeoutException as error:
            raise _unavailable_error(error) from error
        except httpx.HTTPError as error:
            raise _unavailable_error(error) from error


def _translate(error: DomainError) -> DomainError:
    """Re-map cloud error codes onto local ones, keeping status and retryability."""
    mapping = {
        "MODEL_AUTH_FAILED": ("LOCAL_MODEL_AUTH_FAILED", "本地模型服务拒绝了该凭据", 502, False),
        "MODEL_NOT_FOUND": (MODEL_NOT_FOUND_CODE, MODEL_NOT_FOUND_MESSAGE, 503, True),
        "MODEL_TIMEOUT": (UNAVAILABLE_CODE, UNAVAILABLE_MESSAGE, 503, True),
        "MODEL_UNAVAILABLE": (UNAVAILABLE_CODE, UNAVAILABLE_MESSAGE, 503, True),
        "MODEL_PROTOCOL_ERROR": (PROTOCOL_CODE, PROTOCOL_MESSAGE, 502, False),
    }
    replacement = mapping.get(error.code)
    if replacement is None:
        return error
    code, message, status, retryable = replacement
    return DomainError(code, message, status, retryable)
