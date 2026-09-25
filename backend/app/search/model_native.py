from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from app.core.llm import ModelConfig
from app.schemas.web_search import (
    NormalizedSearchResult,
    SearchConnectionResult,
    SearchRequest,
    SearchResponse,
)
from app.search.provider import SearchProviderError

_CITATION_PATTERN = re.compile(r"\[ref_(\d+)\]")
_CONTENT_LIMIT = 50 * 1024
_SNIPPET_LIMIT = 10_000


@dataclass(frozen=True)
class NativeSearchSupport:
    kind: Literal["dashscope", "openai"]
    label: str


def detect_native_search(config: ModelConfig) -> NativeSearchSupport | None:
    """Return native web-search support for the configured model endpoint, if known."""
    host = (urlsplit(config.base_url).hostname or "").lower()
    model = config.model.strip()
    if not host or not model:
        return None
    if _is_dashscope_host(host):
        return NativeSearchSupport("dashscope", f"模型内置联网（{model}）")
    if host == "api.openai.com":
        return NativeSearchSupport("openai", f"OpenAI 内置联网（{model}）")
    return None


def _is_dashscope_host(host: str) -> bool:
    if host == "dashscope.aliyuncs.com" or host.endswith(".maas.aliyuncs.com"):
        return True
    return host.endswith(".aliyuncs.com") and host.startswith("dashscope-")


class ModelSearchProvider:
    """Web search executed by the chat model provider itself (no separate search API key)."""

    name = "model"

    def __init__(
        self,
        credentials: Callable[[], tuple[ModelConfig, str | None]],
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.credentials = credentials
        self.client = client

    def _resolve(self) -> tuple[ModelConfig, str, NativeSearchSupport] | None:
        try:
            config, api_key = self.credentials()
        except Exception:  # noqa: BLE001 - keyring and settings errors mean unavailable
            return None
        if not api_key or not config.base_url or not config.model.strip():
            return None
        support = detect_native_search(config)
        if support is None:
            return None
        return config, api_key, support

    def available(self) -> bool:
        return self._resolve() is not None

    async def search(self, request: SearchRequest) -> SearchResponse:
        resolved = self._resolve()
        if resolved is None:
            raise SearchProviderError(
                "SEARCH_AUTH_FAILED", "模型内置联网当前不可用", retryable=False
            )
        config, api_key, support = resolved
        if support.kind == "dashscope":
            return await self._search_dashscope(config, api_key, request)
        return await self._search_openai(config, api_key, request)

    async def test_connection(self) -> SearchConnectionResult:
        resolved = self._resolve()
        if resolved is None:
            return SearchConnectionResult(
                ok=False, provider=self.name, message="模型内置联网当前不可用"
            )
        try:
            await self.search(SearchRequest(query="DocMind 联网搜索测试", max_results=1))
        except Exception:  # noqa: BLE001 - connection test reports failure instead of raising
            return SearchConnectionResult(
                ok=False, provider=self.name, message="模型内置联网连接失败"
            )
        return SearchConnectionResult(
            ok=True, provider=self.name, message=f"{resolved[2].label} 可用"
        )

    async def _search_dashscope(
        self, config: ModelConfig, api_key: str, request: SearchRequest
    ) -> SearchResponse:
        payload = {
            "model": config.model,
            "messages": [{"role": "user", "content": request.query}],
            "stream": False,
            "enable_search": True,
            "search_options": {
                "enable_source": True,
                "enable_citation": True,
                "citation_format": "[ref_ ]",
            },
        }
        url = f"{config.base_url.rstrip('/')}/chat/completions"
        response = await self._post(url, payload, api_key, config.timeout_seconds)
        data = _json_object(response)
        choices = data.get("choices")
        answer = ""
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, str):
                answer = content
        search_info = data.get("search_info")
        if not isinstance(search_info, dict):
            output = data.get("output")
            search_info = output.get("search_info") if isinstance(output, dict) else None
        items = search_info.get("search_results") if isinstance(search_info, dict) else None
        return _dashscope_results(items if isinstance(items, list) else [], answer, request.max_results)

    async def _search_openai(
        self, config: ModelConfig, api_key: str, request: SearchRequest
    ) -> SearchResponse:
        url = f"{config.base_url.rstrip('/')}/responses"
        payload: dict[str, Any] = {
            "model": config.model,
            "input": request.query,
            "tools": [{"type": "web_search"}],
        }
        response = await self._post(url, payload, api_key, config.timeout_seconds)
        if response.status_code == 400:
            payload["tools"] = [{"type": "web_search_preview"}]
            response = await self._post(url, payload, api_key, config.timeout_seconds)
        return _openai_results(_json_object(response), request.max_results)

    async def _post(
        self, url: str, payload: dict[str, Any], api_key: str, timeout_seconds: float
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        own = self.client is None
        client = self.client or httpx.AsyncClient(timeout=timeout_seconds)
        try:
            response = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as error:
            raise SearchProviderError("SEARCH_PROVIDER_TIMEOUT", "联网搜索响应超时") from error
        except httpx.HTTPError as error:
            raise SearchProviderError("SEARCH_PROVIDER_ERROR", "联网搜索请求失败") from error
        finally:
            if own:
                await client.aclose()
        _raise_for_status(response)
        return response


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    if response.status_code in (401, 403):
        raise SearchProviderError("SEARCH_AUTH_FAILED", "模型联网搜索认证失败", retryable=False)
    if response.status_code == 429:
        raise SearchProviderError("SEARCH_PROVIDER_RATE_LIMITED", "模型联网搜索请求过于频繁")
    raise SearchProviderError(
        "SEARCH_PROVIDER_ERROR",
        "模型联网搜索暂时不可用",
        retryable=response.status_code >= 500,
    )


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as error:
        raise SearchProviderError(
            "SEARCH_PROVIDER_PROTOCOL_ERROR", "联网搜索返回了无法识别的数据", retryable=False
        ) from error
    if not isinstance(data, dict):
        raise SearchProviderError(
            "SEARCH_PROVIDER_PROTOCOL_ERROR", "联网搜索返回了无法识别的数据", retryable=False
        )
    return data


def _dashscope_results(
    items: list[Any], answer: str, max_results: int
) -> SearchResponse:
    segments = _citation_segments(answer)
    results: list[NormalizedSearchResult] = []
    for position, item in enumerate(items):
        if len(results) >= max_results:
            break
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        try:
            index = int(item.get("index", position + 1))
        except (TypeError, ValueError):
            index = position + 1
        segment = segments.get(index, "")
        snippet_source = segment or answer
        content = segment or answer
        result = _normalized_result(
            rank=len(results) + 1,
            url=url,
            title=item.get("title") if isinstance(item.get("title"), str) else url,
            snippet=snippet_source,
            content=content,
        )
        if result is not None:
            results.append(result)
    if not results:
        raise SearchProviderError("SEARCH_PROVIDER_ERROR", "模型没有返回可用的联网搜索结果")
    return SearchResponse(results=results, provider="model:dashscope")


def _openai_results(data: dict[str, Any], max_results: int) -> SearchResponse:
    output = data.get("output")
    results: list[NormalizedSearchResult] = []
    seen: set[str] = set()
    if isinstance(output, list):
        for item in output:
            if len(results) >= max_results:
                break
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            content_parts = item.get("content")
            if not isinstance(content_parts, list):
                continue
            for part in content_parts:
                if len(results) >= max_results:
                    break
                if not isinstance(part, dict) or part.get("type") != "output_text":
                    continue
                text = part.get("text") if isinstance(part.get("text"), str) else ""
                annotations = part.get("annotations")
                if not isinstance(annotations, list):
                    continue
                for annotation in annotations:
                    if len(results) >= max_results:
                        break
                    if not isinstance(annotation, dict) or annotation.get("type") != "url_citation":
                        continue
                    url = annotation.get("url")
                    if not isinstance(url, str) or not url.strip() or url in seen:
                        continue
                    excerpt = _citation_excerpt(text, annotation)
                    title = annotation.get("title")
                    seen.add(url)
                    result = _normalized_result(
                        rank=len(results) + 1,
                        url=url,
                        title=title if isinstance(title, str) and title.strip() else url,
                        snippet=excerpt or text,
                        content=excerpt or text,
                    )
                    if result is not None:
                        results.append(result)
    if not results:
        raise SearchProviderError("SEARCH_PROVIDER_ERROR", "模型没有返回可用的联网搜索结果")
    return SearchResponse(results=results, provider="model:openai")


def _citation_excerpt(text: str, annotation: dict[str, Any]) -> str:
    start = annotation.get("start_index")
    end = annotation.get("end_index")
    if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(text):
        return text[start:end].strip()
    return ""


def _citation_segments(answer: str) -> dict[int, str]:
    """Map each citation index to the answer text that the citation marker follows."""
    matches = list(_CITATION_PATTERN.finditer(answer))
    if not matches:
        return {}
    segments: dict[int, str] = {}
    previous_end = 0
    for match in matches:
        text = _clean_fragment(answer[previous_end : match.start()])
        previous_end = match.end()
        if text:
            _append_segment(segments, int(match.group(1)), text)
    tail = _clean_fragment(answer[previous_end:])
    if tail:
        _append_segment(segments, int(matches[-1].group(1)), tail)
    return segments


def _clean_fragment(value: str) -> str:
    return value.strip().lstrip("，。；、,;.")


def _append_segment(segments: dict[int, str], index: int, text: str) -> None:
    existing = segments.get(index)
    segments[index] = f"{existing} {text}".strip() if existing else text


def _normalized_result(
    *, rank: int, url: str, title: str, snippet: str, content: str
) -> NormalizedSearchResult | None:
    try:
        return NormalizedSearchResult(
            rank=rank,
            canonical_url=url.strip(),
            title=_clean(title, 512) or url.strip(),
            snippet=_clean(snippet, _SNIPPET_LIMIT),
            content=_clean(content, _CONTENT_LIMIT),
        )
    except ValueError:
        return None


def _clean(value: str, limit: int) -> str:
    return value.strip()[:limit]
