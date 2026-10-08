from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, Field, field_validator

from app.api.errors import DomainError
from app.core.model_capabilities import (
    accepts_temperature,
    clamp_temperature,
    reasoning_params,
    supports_tool_calling,
)
from app.schemas.common import WireModel


class ModelConfig(WireModel):
    preset: str
    base_url: str
    model: str
    timeout_seconds: float = Field(gt=0, le=300)
    # Unified reasoning-effort level persisted from the settings form. Empty
    # means "use the vendor's own default" and sends no vendor-specific field.
    reasoning_effort: str = ""

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        if value == "":
            return value
        if value != value.strip() or any(character.isspace() for character in value):
            raise ValueError("invalid model base URL")
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as error:
            raise ValueError("invalid model base URL") from error
        scheme = parsed.scheme.lower()
        if (
            scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or "?" in value
            or parsed.fragment
            or "#" in value
        ):
            raise ValueError("invalid model base URL")
        host = parsed.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
        netloc = host if port is None or default_port else f"{host}:{port}"
        return urlunsplit((scheme, netloc, parsed.path.rstrip("/"), "", ""))


class ModelConnectionResult(WireModel):
    connected: bool
    latency_ms: int


# Vendors disagree on the valid temperature range: Zhipu documents (0, 1) and
# explicitly rejects `temperature = 0`, while DeepSeek/Qwen/Moonshot accept 0.
# Probing the connection therefore uses the smallest value every known vendor
# accepts instead of an absolute zero.
CONNECTION_TEST_TEMPERATURE = 0.1


# 🔴 下面这几个 DTO 故意用 `BaseModel` 而不是 `WireModel`。
# `WireModel` 带 `alias_generator=to_camel` + `serialize_by_alias=True`，于是
# `LLMMessage(tool_call_id=...)` 会被 `model_dump()` 序列化成 `toolCallId`，
# 而 OpenAI 兼容接口要的是 `tool_call_id` —— 一次改名就把工具调用打挂，
# 且因为字段名合法而**静默**失败（服务端只会回 400，看不出是别名问题）。
# 这几个类是本进程与厂商之间的内部传输结构，不参与 DocMind 自己的 HTTP 契约，
# 所以中性名（snake_case）才是正确形态。
class LLMToolCall(BaseModel):
    """A complete tool call the model asked for, arguments as the raw JSON string
    the vendor produced (never re-serialized, so a malformed payload stays
    observable instead of being silently coerced)."""

    id: str = ""
    name: str = ""
    arguments: str = ""


class LLMToolCallDelta(BaseModel):
    """One streamed fragment of a tool call.

    Vendors split a single call across frames: `index` selects which call,
    `id`/`name` normally arrive once, and `arguments` arrives in arbitrary
    slices that must be concatenated in order.
    """

    index: int = 0
    id: str = ""
    name: str = ""
    arguments: str = ""


class LLMToolSpec(BaseModel):
    """An OpenAI-shaped `function` tool declaration offered to the model."""

    name: str
    description: str
    parameters: dict[str, Any]

    def to_wire(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class LLMMessage(BaseModel):
    role: str
    content: str = ""
    # Set on the `assistant` message that requests tools, and echoed on the
    # `tool` message that answers each one. The vendor matches them by id.
    tool_call_id: str = ""
    tool_calls: list[LLMToolCall] = []

    def to_wire(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in self.tool_calls
            ]
        if self.tool_call_id:
            payload["tool_call_id"] = self.tool_call_id
        return payload


class ChatRequest(BaseModel):
    messages: list[LLMMessage]
    temperature: float = 0.2
    # Unified reasoning-effort level. Translated to the vendor's own field by
    # `reasoning_params`; ignored when the vendor does not declare support.
    reasoning_effort: str | None = None
    # Empty means "send no `tools` field at all". The caller decides — it is the
    # only layer that knows whether the user allowed web access this turn — and
    # `OpenAICompatibleProvider.stream_chat` retries without the field when a
    # model rejects it.
    tools: list[LLMToolSpec] = []


class ChatDelta(BaseModel):
    content: str = ""
    tool_calls: list[LLMToolCallDelta] = []


class ToolCallAccumulator:
    """Rebuilds complete tool calls from streamed fragments."""

    def __init__(self) -> None:
        self._calls: dict[int, LLMToolCall] = {}

    def add(self, deltas: list[LLMToolCallDelta]) -> None:
        for delta in deltas:
            call = self._calls.setdefault(delta.index, LLMToolCall())
            if delta.id:
                call.id = delta.id
            if delta.name:
                call.name = delta.name
            if delta.arguments:
                call.arguments += delta.arguments

    def complete(self) -> list[LLMToolCall]:
        # A vendor that never sends an id still needs one back on the matching
        # `tool` message, so synthesise a stable placeholder instead of echoing
        # an empty string the vendor cannot match.
        return [
            call.model_copy(update={"id": call.id or f"call_{index}"})
            for index, call in sorted(self._calls.items())
        ]


class LLMProvider(Protocol):
    async def test_connection(self) -> ModelConnectionResult: ...

    def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatDelta]: ...


class AvailableModel(WireModel):
    """One model id a provider offers, as reported by its OpenAI-compatible
    `GET /models` endpoint."""

    id: str
    label: str


class OpenAICompatibleProvider:
    def __init__(self, config: ModelConfig, api_key: str) -> None:
        self.config = config
        self.api_key = api_key

    @property
    def _url(self) -> str:
        return f"{self.config.base_url.rstrip('/')}/chat/completions"

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        # An empty token means the provider needs no auth (local servers), and
        # `Bearer ` with a blank credential is at best noise and at worst a
        # rejected request on a server that does check.
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def list_models(self) -> list[AvailableModel]:
        """List the models the configured provider offers.

        Used to let the user pick a model instead of typing an id from memory.
        A provider that has no `/models` endpoint yields an empty list rather
        than an error, so manual entry stays available everywhere.
        """
        if not self.config.base_url:
            return []
        url = f"{self.config.base_url.rstrip('/')}/models"
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                response = await client.get(url, headers=self._headers)
        except httpx.TimeoutException as error:
            raise _model_error("MODEL_TIMEOUT") from error
        except httpx.HTTPError as error:
            raise _model_error("MODEL_UNAVAILABLE") from error
        # A provider without a models endpoint is a soft failure: the user can
        # still type a model id manually.
        if response.status_code in (404, 405):
            return []
        _raise_for_status(response)
        try:
            payload = response.json()
            entries = payload["data"]
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise _model_error("MODEL_PROTOCOL_ERROR") from error
        # `data: null` and `data: []` both mean "this provider currently offers
        # nothing" — a normal state for a local server with no model loaded.
        # Rejecting them as protocol errors made a fresh install look broken.
        if entries is None:
            return []
        if not isinstance(entries, list):
            raise _model_error("MODEL_PROTOCOL_ERROR")
        models: list[AvailableModel] = []
        for entry in entries:
            model_id = entry.get("id") if isinstance(entry, dict) else None
            if not isinstance(model_id, str) or not model_id.strip():
                continue
            trimmed = model_id.strip()
            models.append(AvailableModel(id=trimmed, label=model_label(trimmed)))
        return models

    async def test_connection(self) -> ModelConnectionResult:
        payload: dict[str, object] = {
            "model": self.config.model,
            "messages": [{"role": "user", "content": "请回复“连接成功”。"}],
            "stream": False,
        }
        # Same constraint as stream_chat: a model that rejects temperature would
        # fail the probe with a 400 that looks like a bad key, not a bad field.
        if accepts_temperature(self.config.preset, self.config.model):
            # Probe with the lowest broadly-accepted temperature instead of
            # absolute zero, which Zhipu rejects outright.
            payload["temperature"] = CONNECTION_TEST_TEMPERATURE
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                response = await client.post(self._url, json=payload, headers=self._headers)
        except httpx.TimeoutException as error:
            raise _model_error("MODEL_TIMEOUT") from error
        except httpx.HTTPError as error:
            raise _model_error("MODEL_UNAVAILABLE") from error
        _raise_for_status(response)
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (IndexError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise _model_error("MODEL_PROTOCOL_ERROR") from error
        if not isinstance(content, str) or not content:
            raise _model_error("MODEL_PROTOCOL_ERROR")
        return ModelConnectionResult(
            connected=True, latency_ms=round((time.perf_counter() - started) * 1000)
        )

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatDelta]:
        """Stream one answer, degrading to plain chat if the model rejects `tools`.

        `tools` is a base OpenAI-compatible field, so unlike `reasoning_effort` it
        is sent for every model rather than only for registered ones — see
        `model_capabilities.supports_tool_calling`. A model that nonetheless
        rejects the field answers 400, which arrives before any delta is emitted;
        retrying once without `tools` turns "this model cannot call tools" into a
        plain answer instead of a failed request.
        """
        emitted = False
        try:
            async for delta in self._stream_once(request, request.tools):
                emitted = True
                yield delta
        except DomainError as error:
            # Only a 4xx protocol rejection is worth retrying, only when `tools`
            # was actually the thing being sent, and only if the first attempt
            # produced nothing — otherwise the retry would duplicate output.
            if emitted or not request.tools or error.code != "MODEL_PROTOCOL_ERROR":
                raise
            async for delta in self._stream_once(request, []):
                yield delta

    async def _stream_once(
        self, request: ChatRequest, tools: list[LLMToolSpec]
    ) -> AsyncIterator[ChatDelta]:
        payload: dict[str, object] = {
            "model": self.config.model,
            "messages": [message.to_wire() for message in request.messages],
            "stream": True,
        }
        # 能力判定放在这里而不是调用方 —— 只有 provider 自己知道 preset/model。
        # 登记为不支持时直接不发；未登记但实际拒绝的组合由 `stream_chat` 的
        # 「去掉 tools 重试一次」兜底。
        if tools and supports_tool_calling(self.config.preset, self.config.model):
            payload["tools"] = [tool.to_wire() for tool in tools]
        # Not every model accepts temperature. OpenAI's reasoning family rejects
        # it outright (gpt-5.5: "Unsupported parameter: 'temperature'"; the 5.6
        # family accepts only the default 1; GPT-6 Astra requires omitting it),
        # which is a 400 no matter what we do with reasoning_effort. Registered
        # models keep it clamped into the range every vendor accepts.
        if accepts_temperature(self.config.preset, self.config.model):
            payload["temperature"] = clamp_temperature(request.temperature)
        # Only models whose docs confirm the field receive reasoning controls;
        # anything unregistered gets nothing extra, so a wrong field name can
        # never provoke a 400.
        effort = request.reasoning_effort or self.config.reasoning_effort
        if effort:
            payload.update(reasoning_params(self.config.preset, self.config.model, effort))
        try:
            async with (
                httpx.AsyncClient(timeout=self.config.timeout_seconds) as client,
                client.stream("POST", self._url, json=payload, headers=self._headers) as response,
            ):
                _raise_for_status(response)
                if (
                    not response.headers.get("content-type", "")
                    .lower()
                    .startswith("text/event-stream")
                ):
                    raise _model_error("MODEL_PROTOCOL_ERROR")
                emitted = False
                async for event in _sse_events(response):
                    if event == "[DONE]":
                        break
                    parts = _delta_parts(event)
                    if parts is None:
                        # Structurally textless frame (usage-only, reasoning-only,
                        # or a shape we do not recognize): vendors legitimately
                        # emit all three mid-stream.
                        continue
                    content, tool_calls = parts
                    if content is not _NO_CONTENT and not isinstance(content, str):
                        # `content` is present but not a string: corrupt, not a
                        # frame we can skip over.
                        raise _model_error("MODEL_PROTOCOL_ERROR")
                    emitted = True
                    yield ChatDelta(
                        content=content if isinstance(content, str) else "",
                        tool_calls=tool_calls,
                    )
                # A stream that produced neither text nor a tool call is
                # unusable, whether or not the vendor closed it with `[DONE]`.
                # Tool-call frames count: a model that answers purely by calling
                # a tool emits no text at all, and treating that as a bad stream
                # turned a working tool call into MODEL_PROTOCOL_ERROR.
                if not emitted:
                    raise _model_error("MODEL_PROTOCOL_ERROR")
        except httpx.TimeoutException as error:
            raise _model_error("MODEL_TIMEOUT") from error
        except httpx.HTTPError as error:
            raise _model_error("MODEL_UNAVAILABLE") from error


# Marks an SSE frame that carries no text delta at all, as opposed to one that
# carries a `content` value of an unexpected type.
_NO_CONTENT = object()


def _delta_parts(event: str) -> tuple[object, list[LLMToolCallDelta]] | None:
    """Extract text and tool-call fragments from one SSE `data:` payload.

    Returns `None` for a frame carrying neither — usage-only, reasoning-only, the
    role-only opening frame, or a shape we do not recognize; vendors legitimately
    emit all of those mid-stream. Otherwise returns `(content, tool_calls)`, where
    `content` is the text or `_NO_CONTENT` for a tool-call-only frame. A `content`
    value that is present but not a string is returned as-is so the caller can
    reject it as corrupt instead of silently dropping it.
    """
    try:
        data = json.loads(event)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    delta = first.get("delta")
    if not isinstance(delta, dict):
        return None
    content = delta.get("content")
    # Absent, null and empty-string content are all "no text this frame".
    text: object = _NO_CONTENT if content is None or content == "" else content
    tool_calls = _tool_call_deltas(delta.get("tool_calls"))
    if text is _NO_CONTENT and not tool_calls:
        return None
    return text, tool_calls


def _tool_call_deltas(raw: object) -> list[LLMToolCallDelta]:
    """Parse a `delta.tool_calls` array, skipping entries that are unreadable.

    A malformed entry is dropped rather than raised on: the frame's text (if any)
    is still usable, and one odd fragment should not kill the whole answer. The
    accumulator concatenates whatever survives.
    """
    if not isinstance(raw, list):
        return []
    deltas: list[LLMToolCallDelta] = []
    for position, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        function = entry.get("function")
        if not isinstance(function, dict):
            function = {}
        index = entry.get("index")
        identifier = entry.get("id")
        name = function.get("name")
        arguments = function.get("arguments")
        # A few OpenAI-compatible servers send `arguments` already parsed as an
        # object even while streaming. Re-serialize so downstream always sees the
        # raw JSON string the wire contract promises.
        if isinstance(arguments, dict):
            arguments = json.dumps(arguments, ensure_ascii=False)
        deltas.append(
            LLMToolCallDelta(
                index=index if isinstance(index, int) else position,
                id=identifier if isinstance(identifier, str) else "",
                name=name if isinstance(name, str) else "",
                arguments=arguments if isinstance(arguments, str) else "",
            )
        )
    return deltas


def model_label(model_id: str) -> str:
    """Humanize a model id for display, keeping the raw id as the shown value
    when nothing better exists so the user always sees what will be sent."""
    friendly = {
        "kimi-k3": "Kimi K3",
        "kimi-k2.6": "Kimi K2.6",
        "kimi-k2.5": "Kimi K2.5",
        "kimi-k2.7-code": "Kimi K2.7 Code",
        "glm-5.3": "GLM-5.3",
        "glm-5.3-flash": "GLM-5.3 Flash",
        "glm-5.2": "GLM-5.2",
        "glm-5.1": "GLM-5.1",
        "glm-5": "GLM-5",
        "glm-4.7": "GLM-4.7",
        "glm-4.6": "GLM-4.6",
        "glm-4.5": "GLM-4.5",
        "mimo-v2.6-pro": "MiMo V2.6 Pro",
        "mimo-v2.6-flash": "MiMo V2.6 Flash",
        "mimo-v2.5-pro": "MiMo V2.5 Pro",
        "mimo-v2.5": "MiMo V2.5",
        "deepseek-flash": "DeepSeek Flash",
        "deepseek-v4-pro": "DeepSeek V4 Pro",
        "deepseek-chat": "DeepSeek Chat",
        "deepseek-reasoner": "DeepSeek Reasoner",
        "qwen3.8-max": "通义千问 3.8 Max",
        "qwen3.8-flash": "通义千问 3.8 Flash",
        "qwen3.7-plus": "通义千问 3.7 Plus",
        "qwen3.7-flash": "通义千问 3.7 Flash",
        "qwen3.6-plus": "通义千问 3.6 Plus",
        "qwen3.5-plus": "通义千问 3.5 Plus",
        "qwen3.5-flash": "通义千问 3.5 Flash",
        "qwen-plus": "通义千问 Plus",
        "qwen-max": "通义千问 Max",
        "gpt-6-astra": "GPT-6 Astra",
        "gpt-6-luna": "GPT-6 Luna",
        "gpt-6.1-sol": "GPT-6.1 Sol",
        "gpt-5.6-sol": "GPT-5.6 Sol",
        "gpt-5.6-terra": "GPT-5.6 Terra",
        "gpt-5.6-luna": "GPT-5.6 Luna",
        "gpt-5.5": "GPT-5.5",
        "gpt-5.4": "GPT-5.4",
        "gpt-5.4-mini": "GPT-5.4 mini",
        "gpt-5.4-nano": "GPT-5.4 nano",
        "gpt-5-mini": "GPT-5 mini",
        "big-pickle": "Big Pickle",
    }
    return friendly.get(model_id, model_id)


async def _sse_events(response: httpx.Response) -> AsyncIterator[str]:
    data_lines: list[str] = []
    async for raw_line in response.aiter_lines():
        line = raw_line.rstrip("\r")
        if not line:
            if data_lines:
                yield "\n".join(data_lines)
                data_lines.clear()
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif line.startswith(("event:", "id:", "retry:", ":")):
            # Named events, ids, retry hints and `:comment` keep-alives carry no
            # payload for us and are legal SSE.
            continue
        else:
            raise _model_error("MODEL_PROTOCOL_ERROR")
    if data_lines:
        # The vendor closed the connection without the trailing blank line that
        # would have flushed the last event. Emit it rather than discarding
        # content the model already produced.
        yield "\n".join(data_lines)


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    if response.status_code in (401, 403):
        raise _model_error("MODEL_AUTH_FAILED")
    if response.status_code == 404:
        raise _model_error("MODEL_NOT_FOUND")
    if response.status_code == 429:
        raise _model_error("MODEL_RATE_LIMITED")
    if 400 <= response.status_code <= 499:
        raise _model_error("MODEL_PROTOCOL_ERROR")
    if 500 <= response.status_code <= 599:
        raise _model_error("MODEL_UNAVAILABLE")
    raise _model_error("MODEL_UNAVAILABLE")


def _model_error(code: str) -> DomainError:
    errors = {
        "MODEL_AUTH_FAILED": (
            "模型服务认证失败，请检查 API Key",
            401,
            False,
            "检查 API Key 后重试",
        ),
        "MODEL_NOT_FOUND": ("未找到指定模型，请检查模型名称", 404, False, "修改模型名称后重试"),
        "MODEL_RATE_LIMITED": ("模型服务请求过于频繁，请稍后重试", 429, True, "稍后重试"),
        "MODEL_TIMEOUT": ("模型服务响应超时，请稍后重试", 504, True, "检查网络或增大超时时间"),
        "MODEL_PROTOCOL_ERROR": ("模型服务返回了无法识别的数据", 502, False, "检查兼容接口配置"),
        "MODEL_UNAVAILABLE": ("模型服务暂时不可用，请稍后重试", 503, True, "稍后重试"),
    }
    message, status_code, retryable, action = errors[code]
    return DomainError(code, message, status_code, retryable, action)
