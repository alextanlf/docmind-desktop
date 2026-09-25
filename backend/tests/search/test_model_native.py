from __future__ import annotations

import json

import httpx
import pytest

from app.core.llm import ModelConfig
from app.schemas.web_search import SearchRequest
from app.search.model_native import ModelSearchProvider, detect_native_search


def _config(base_url: str, model: str = "qwen-plus") -> ModelConfig:
    return ModelConfig(preset="qwen", base_url=base_url, model=model, timeout_seconds=30)


def _dashscope_payload() -> dict:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": (
                        "杭州明天多云转晴[ref_1]，气温 24℃ 至 34℃[ref_2]。"
                        "出行建议携带薄外套[ref_1]。"
                    ),
                }
            }
        ],
        "search_info": {
            "search_results": [
                {"index": 1, "title": "杭州天气预报", "url": "https://example.com/hz"},
                {"index": 2, "title": "浙江天气", "url": "https://example.com/zj"},
            ]
        },
    }


def test_detect_native_search_for_known_endpoints() -> None:
    assert detect_native_search(_config("https://dashscope.aliyuncs.com/compatible-mode/v1"))
    assert detect_native_search(_config("https://team.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"))
    assert detect_native_search(_config("https://dashscope-intl.aliyuncs.com/compatible-mode/v1"))
    assert detect_native_search(_config("https://dashscope.example.com/v1")) is None
    assert (
        detect_native_search(_config("https://api.openai.com/v1", "gpt-5-mini")).kind == "openai"
    )
    assert detect_native_search(_config("https://api.deepseek.com/v1", "deepseek-chat")) is None
    assert detect_native_search(_config("http://127.0.0.1:11434/v1", "qwen3")) is None


@pytest.mark.asyncio
async def test_dashscope_search_returns_cited_sources_without_ref_markers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        payload = json.loads(request.read().decode())
        assert payload["enable_search"] is True
        assert payload["search_options"]["enable_source"] is True
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(200, json=_dashscope_payload())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = ModelSearchProvider(
        lambda: (_config("https://dashscope.aliyuncs.com/compatible-mode/v1"), "secret"),
        client=client,
    )
    response = await provider.search(SearchRequest(query="杭州天气", max_results=5))
    await client.aclose()

    assert response.provider == "model:dashscope"
    assert [str(item.canonical_url) for item in response.results] == [
        "https://example.com/hz",
        "https://example.com/zj",
    ]
    assert response.results[0].snippet.startswith("杭州明天多云转晴")
    assert "ref_" not in response.results[0].content
    assert "薄外套" in response.results[0].content
    assert "24℃ 至 34℃" in response.results[1].content


@pytest.mark.asyncio
async def test_model_search_unavailable_without_key_or_unknown_endpoint() -> None:
    provider = ModelSearchProvider(lambda: (_config("https://api.deepseek.com/v1", "deepseek-chat"), "secret"))
    assert provider.available() is False
    provider_without_key = ModelSearchProvider(
        lambda: (_config("https://dashscope.aliyuncs.com/compatible-mode/v1"), None)
    )
    assert provider_without_key.available() is False
