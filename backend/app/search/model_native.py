from __future__ import annotations

import re
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from app.core.llm import ModelConfig
from app.schemas.web_search import (
    NormalizedSearchResult,
    SearchRequest,
    SearchResponse,
)
from app.search.provider import SearchProviderError

_CITATION_PATTERN = re.compile(r"\[ref_(\d+)\]")
_CONTENT_LIMIT = 50 * 1024
_SNIPPET_LIMIT = 10_000


@dataclass(frozen=True)
class NativeSearchSupport:
    kind: Literal["dashscope", "openai", "glm", "mimo"]
    label: str


def detect_native_search(config: ModelConfig) -> NativeSearchSupport | None:
    """按**协议形状**而非厂商名气判定模型内置联网是否可用。

    要能用，必须同时知道两件事：怎么**开启**服务端搜索，以及来源列表**落在响应的哪一层**。
    所以这是一张"形状白名单"，不是"厂商白名单"。

    **为什么不登记别的厂商**（逐条核实过官方文档，不是猜的）：

    - **DeepSeek**：官方 `/responses` 文档写明 `tools` 只接受 `function`，
      「**内置工具类型会被忽略**」，且把 `web_search` 与 `file_search` /
      `code_interpreter` / `computer_use` / `mcp` 一起列为 Ignored。
      ⚠️ 若干第三方站点称它"支持 web_search"，与官方原文矛盾，**不要采信**。
      它自带的联网只存在于网页版与 Claude Code 的 Anthropic 兼容通道，不是本接口能力。
    - **Kimi（Moonshot）**：确实有内置的 `$web_search`（`tools` 里声明
      `{"type": "builtin_function", "function": {"name": "$web_search"}}`），但
      (a) 官方文档顶部自称"正在更新、近期不建议使用、本文档已过时"，
      (b) 它的用法是"把模型给的 arguments 原样回传"，搜索在厂商内部完成，
      **不返回可枚举的来源列表** —— 没有 URL 就没法归一化成引用来源。
    - **OpenCode Zen/Go**：模型网关，官方文档通篇没有搜索能力。
    - 其余 `custom` / 本地推理服务：未知端点，猜错字段名只会得到 400。
    """
    host = (urlsplit(config.base_url).hostname or "").lower()
    model = config.model.strip()
    if not host or not model:
        return None
    if _is_dashscope_host(host):
        return NativeSearchSupport("dashscope", f"模型内置联网（{model}）")
    if host == "api.openai.com":
        return NativeSearchSupport("openai", f"OpenAI 内置联网（{model}）")
    if _is_glm_host(host):
        return NativeSearchSupport("glm", f"模型内置联网（{model}）")
    if _is_mimo_host(host):
        return NativeSearchSupport("mimo", f"模型内置联网（{model}）")
    return None


def _is_dashscope_host(host: str) -> bool:
    if host == "dashscope.aliyuncs.com" or host.endswith(".maas.aliyuncs.com"):
        return True
    return host.endswith(".aliyuncs.com") and host.startswith("dashscope-")


def _is_glm_host(host: str) -> bool:
    """智谱：国内 `open.bigmodel.cn`，海外 `api.z.ai`。"""
    return host in {"open.bigmodel.cn", "api.z.ai"} or host.endswith(".bigmodel.cn")


def _is_mimo_host(host: str) -> bool:
    """小米 MiMo：标准通道 `api.xiaomimimo.com`，另有 Token Plan 通道。"""
    return host.endswith(".xiaomimimo.com")


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
        if support.kind == "glm":
            return await self._search_glm(config, api_key, request)
        if support.kind == "mimo":
            return await self._search_mimo(config, api_key, request)
        return await self._search_openai(config, api_key, request)

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

    async def _search_glm(
        self, config: ModelConfig, api_key: str, request: SearchRequest
    ) -> SearchResponse:
        """智谱：`tools` 里声明 web_search，来源随响应一起返回。

        官方示例（docs.bigmodel.cn/cn/guide/tools）的字段：
        `{"type": "web_search", "web_search": {"enable": True, "search_engine":
        "search_pro", "search_result": True, "count": N}}`。
        `search_result` 是拿到"详细来源信息"的开关 —— 不开就只有一段生成好的摘要，
        没有可引用的 URL。`content_size: high` 让厂商把每条来源的摘要给到最长，
        这段摘要是直接进 prompt 的证据，短了就等于少给模型信息（默认是 medium）。
        `search_prompt` 不传：那是让厂商拿结果再生成一遍摘要用的，
        我们只要原始来源，多传一次等于白花一次生成费用。
        """
        payload = {
            "model": config.model,
            "messages": [{"role": "user", "content": request.query}],
            "stream": False,
            "tools": [
                {
                    "type": "web_search",
                    "web_search": {
                        "enable": True,
                        "search_engine": "search_pro",
                        "search_result": True,
                        "content_size": "high",
                        "count": request.max_results,
                    },
                }
            ],
        }
        url = f"{config.base_url.rstrip('/')}/chat/completions"
        response = await self._post(url, payload, api_key, config.timeout_seconds)
        return _glm_results(_json_object(response), request.max_results)

    async def _search_mimo(
        self, config: ModelConfig, api_key: str, request: SearchRequest
    ) -> SearchResponse:
        """小米 MiMo：`tools` 里声明 web_search，来源以 `url_citation` 注解返回。

        官方示例（platform.xiaomimimo.com/docs/zh-CN/usage-guide/tool-calling/web-search）：
        `{"type": "web_search", "force_search": True, "limit": N, "max_keyword": M}`。

        `force_search` 必须为真：默认是"意图识别"模式，模型自认为不需要实时信息时
        干脆不搜，我们拿不到任何注解，整轮检索等于白跑。`max_keyword` 不传 ——
        官方说明每个关键词都会**单独计费**（¥16/1K 次），而 DocMind 已经自己做过
        query 改写（`query_planner`），再让 MiMo 并发多关键词是重复劳动加双份钱。

        前置条件：用户必须在控制台开通「联网服务插件」，否则拿不到来源。
        """
        payload = {
            "model": config.model,
            "messages": [{"role": "user", "content": request.query}],
            # 非流式：流式只在首包带来源，而我们要的是完整响应体，非流式一次取全。
            "stream": False,
            "tools": [
                {
                    "type": "web_search",
                    "force_search": True,
                    "limit": request.max_results,
                }
            ],
            "tool_choice": "auto",
        }
        url = f"{config.base_url.rstrip('/')}/chat/completions"
        response = await self._post(url, payload, api_key, config.timeout_seconds)
        return _mimo_results(_json_object(response), request.max_results)

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


# 各家把"来源列表"放在响应的哪一层写法并不一致：智谱官方 `/chat/completions` 的响应 schema
# 把它放在**顶层** `web_search[]`（字段 link/title/content/…），而它的部分文档示例里同样的
# 数据出现在 tool_calls 内部；MiMo 放在 `choices[0].message.annotations[]`
# （字段 url/title/summary/…，与 OpenAI 的 `url_citation` 同形）。
# 写死 JSON 路径的代价是**整家厂商静默失效** —— 请求 200、结果为空、用户只看到"没搜到"，
# 所以这里按结构特征定位：找第一个"元素是 dict 且带非空 URL 字段"的数组。
_URL_KEYS = ("link", "url")
# 只是防病态响应的兜底，不是业务约束 —— 智谱的 tool_calls 变体本身就有 7 层。
_MAX_SOURCE_DEPTH = 12


def _find_source_list(payload: object) -> list[dict[str, Any]]:
    """找出响应里**最浅**的那个"来源列表"。

    广度优先而不是深度优先：厂商本意的那份列表通常在浅层（智谱顶层 `web_search[]`、
    MiMo `message.annotations[]`），而深层可能出现碰巧长得像的数组。先浅后深能拿到
    "本意"的那一份，而不是凑巧匹配的另一处。
    """
    queue: deque[tuple[object, int]] = deque([(payload, 0)])
    while queue:
        node, depth = queue.popleft()
        if depth > _MAX_SOURCE_DEPTH:
            continue
        if isinstance(node, list):
            entries = [item for item in node if isinstance(item, dict)]
            if entries and any(
                isinstance(entry.get(key), str) and entry[key].strip()
                for entry in entries
                for key in _URL_KEYS
            ):
                return entries
            queue.extend((item, depth + 1) for item in node)
        elif isinstance(node, dict):
            queue.extend((value, depth + 1) for value in node.values())
    return []


def _first_string(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _glm_results(data: dict[str, Any], max_results: int) -> SearchResponse:
    return _results_from_items(
        _find_source_list(data),
        max_results,
        provider="model:glm",
        content_keys=("content", "summary", "snippet"),
    )


def _mimo_results(data: dict[str, Any], max_results: int) -> SearchResponse:
    items = [
        item
        for item in _find_source_list(data)
        # 注解带 type；缺失时按 url_citation 处理 —— 字段在版本间并不总是出现。
        if str(item.get("type", "url_citation")) == "url_citation"
    ]
    return _results_from_items(
        items,
        max_results,
        provider="model:mimo",
        content_keys=("summary", "content", "snippet"),
    )


def _results_from_items(
    items: list[dict[str, Any]],
    max_results: int,
    *,
    provider: str,
    content_keys: tuple[str, ...],
) -> SearchResponse:
    results: list[NormalizedSearchResult] = []
    for item in items:
        if len(results) >= max_results:
            break
        url = _first_string(item, *_URL_KEYS)
        if not url:
            continue
        text = _first_string(item, *content_keys)
        result = _normalized_result(
            rank=len(results) + 1,
            url=url,
            title=_first_string(item, "title") or url,
            snippet=text,
            content=text,
        )
        if result is not None:
            results.append(result)
    if not results:
        raise SearchProviderError("SEARCH_PROVIDER_ERROR", "模型没有返回可用的联网搜索结果")
    return SearchResponse(results=results, provider=provider)


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
