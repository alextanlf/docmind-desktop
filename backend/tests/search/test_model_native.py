from __future__ import annotations

import json

import httpx
import pytest

from app.core.llm import ModelConfig
from app.schemas.web_search import SearchRequest
from app.search.model_native import (
    ModelSearchProvider,
    _glm_results,
    _mimo_results,
    detect_native_search,
)
from app.search.provider import SearchProviderError


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
    assert detect_native_search(_config("https://api.openai.com/v1", "gpt-5-mini")).kind == "openai"
    # 智谱：国内 open.bigmodel.cn 与海外 api.z.ai 是同一种形状。
    assert detect_native_search(_config("https://open.bigmodel.cn/api/paas/v4", "glm-4.6")).kind == "glm"
    assert detect_native_search(_config("https://api.z.ai/api/paas/v4", "glm-5.3")).kind == "glm"
    # 小米 MiMo：标准通道与 Token Plan 通道。
    assert detect_native_search(_config("https://api.xiaomimimo.com/v1", "mimo-v2.6-pro")).kind == "mimo"
    assert detect_native_search(_config("https://token-plan-cn.xiaomimimo.com/v1", "mimo-v2.6-pro")).kind == "mimo"
    # 🔴 以下三家是**核实过官方文档后确认不可用**的，别让它们被"看起来像"的改动放进来：
    # DeepSeek 官方 /responses 写「内置工具类型会被忽略」；Kimi 的 $web_search 按官方说法
    # 已过时且不返回来源列表；OpenCode Zen 是模型网关。
    assert detect_native_search(_config("https://api.deepseek.com/v1", "deepseek-chat")) is None
    assert detect_native_search(_config("https://api.moonshot.cn/v1", "kimi-k3")) is None
    assert detect_native_search(_config("https://opencode.ai/zen/v1", "big-pickle")) is None
    assert detect_native_search(_config("http://127.0.0.1:11434/v1", "qwen3")) is None


_GLM_PAYLOAD = {
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "杭州明天多云。"}}],
    "web_search": [
        {
            "title": "杭州天气预报",
            "content": "明天多云转晴，24~34℃。",
            "link": "https://example.com/hz",
            "media": "中国天气网",
            "refer": "ref_1",
            "publish_date": "2026-09-21",
        }
    ],
}

_MIMO_PAYLOAD = {
    "choices": [
        {
            "index": 0,
            "finish_reason": "stop",
            "message": {
                "role": "assistant",
                "content": "武汉明天阴天。",
                "tool_calls": None,
                "annotations": [
                    {
                        "type": "url_citation",
                        "url": "https://example.com/wh",
                        "title": "武汉天气预报",
                        "summary": "武汉天气预报，及时准确发布。",
                        "site_name": "中国天气网",
                        "publish_time": "2026-09-21T08:32:25",
                    }
                ],
            },
        }
    ],
    "usage": {"web_search_usage": {"tool_usage": 3, "page_usage": 3}},
}


@pytest.mark.asyncio
async def test_glm_search_sends_documented_tool_and_parses_top_level_sources() -> None:
    """智谱：`search_result` 是拿到可引用 URL 的开关，漏了它只剩一段没有出处的摘要。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        payload = json.loads(request.read().decode())
        seen.update(payload)
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(200, json=_GLM_PAYLOAD)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = ModelSearchProvider(
        lambda: (_config("https://open.bigmodel.cn/api/paas/v4", "glm-4.6"), "secret"),
        client=client,
    )
    response = await provider.search(SearchRequest(query="杭州天气", max_results=4))
    await client.aclose()

    assert seen["stream"] is False
    tool = seen["tools"][0]
    assert tool["type"] == "web_search"
    assert tool["web_search"]["enable"] is True
    assert tool["web_search"]["search_result"] is True
    # 摘要是直接进 prompt 的证据，默认的 medium 太短，明确要最长的。
    assert tool["web_search"]["content_size"] == "high"
    assert tool["web_search"]["count"] == 4
    assert response.provider == "model:glm"
    assert [str(item.canonical_url) for item in response.results] == ["https://example.com/hz"]
    assert response.results[0].title == "杭州天气预报"
    assert "24~34" in response.results[0].content


@pytest.mark.asyncio
async def test_mimo_search_forces_the_search_and_parses_url_citation_annotations() -> None:
    """MiMo 默认是"意图识别"，不强制的话模型可能压根不搜，我们拿不到任何来源。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read().decode())
        seen.update(payload)
        return httpx.Response(200, json=_MIMO_PAYLOAD)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = ModelSearchProvider(
        lambda: (_config("https://api.xiaomimimo.com/v1", "mimo-v2.6-pro"), "secret"),
        client=client,
    )
    response = await provider.search(SearchRequest(query="武汉天气", max_results=3))
    await client.aclose()

    assert seen["stream"] is False
    assert seen["tool_choice"] == "auto"
    tool = seen["tools"][0]
    assert tool["type"] == "web_search"
    assert tool["force_search"] is True
    assert tool["limit"] == 3
    # 每个关键词单独计费，DocMind 自己已经做过改写，不该再让 MiMo 并发多关键词。
    assert "max_keyword" not in tool
    assert response.provider == "model:mimo"
    assert [str(item.canonical_url) for item in response.results] == ["https://example.com/wh"]
    assert response.results[0].title == "武汉天气预报"
    assert "及时准确" in response.results[0].content


@pytest.mark.parametrize(
    ("parse", "payload"),
    [
        # 厂商本意的来源列表在浅层；深层出现"长得像"的数组时不能选错。
        (
            lambda data: _glm_results(data, 5).results,
            {
                "web_search": [
                    {"title": "顶层", "link": "https://shallow.test/a", "content": "浅"}
                ],
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "web_search": {
                                        "search_results": [
                                            {"title": "深层", "link": "https://deep.test/b"}
                                        ]
                                    }
                                }
                            ]
                        }
                    }
                ],
            },
        ),
        # 智谱的部分文档示例把来源放在 tool_calls 内部，也要能取到。
        (
            lambda data: _glm_results(data, 5).results,
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "type": "web_search",
                                    "web_search": {
                                        "search_results": [
                                            {"title": "T", "link": "https://example.com/t"}
                                        ]
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
        ),
        # URL 字段出现在 `url` 而不是 `link` 时同样成立。
        (
            lambda data: _glm_results(data, 5).results,
            {"web_search": [{"title": "T", "url": "https://example.com/u", "content": "C"}]},
        ),
        # MiMo 注解缺 type 字段（版本差异）不能因此丢结果。
        (
            lambda data: _mimo_results(data, 5).results,
            {
                "choices": [
                    {
                        "message": {
                            "annotations": [
                                {"url": "https://example.com/a", "title": "T", "summary": "S"}
                            ]
                        }
                    }
                ]
            },
        ),
    ],
)
def test_native_source_parsing_tolerates_documented_shape_variants(parse, payload) -> None:
    """这些厂商的文档与真实响应并不总是一致，写死路径会让整家厂商静默失效。"""
    results = parse(payload)
    urls = [str(item.canonical_url) for item in results]
    assert len(urls) == 1
    assert urls[0].startswith("https://")
    # 🔴 必须排除深层那份候选。只断言"是个 https 链接"对深浅两个 URL 都成立，等于没测
    # —— 实测把广度优先改成 `queue.pop()`（退化成深度优先）时，这条断言照样通过。
    assert "https://deep.test/b" not in urls


def test_native_source_parsing_skips_non_url_entries_and_other_annotation_types() -> None:
    glm = _glm_results(
        {
            "web_search": [
                {"title": "数字链接", "link": 123, "content": "c"},
                {"title": "空白链接", "link": "   ", "content": "c"},
                {"title": "正常", "link": "https://ok.test/z", "content": "c"},
            ]
        },
        5,
    ).results
    assert [str(item.canonical_url) for item in glm] == ["https://ok.test/z"]

    mimo = _mimo_results(
        {
            "choices": [
                {
                    "message": {
                        "annotations": [
                            {
                                "type": "file_citation",
                                "url": "https://skip.test/x",
                                "title": "非网页引用",
                            },
                            {
                                "type": "url_citation",
                                "url": "https://keep.test/y",
                                "title": "网页引用",
                                "summary": "S",
                            },
                        ]
                    }
                }
            ]
        },
        5,
    ).results
    assert [str(item.canonical_url) for item in mimo] == ["https://keep.test/y"]


@pytest.mark.parametrize(
    ("parse", "payload"),
    [
        (_glm_results, {"choices": [{"message": {"content": "模型没搜，只答了一句"}}]}),
        (_mimo_results, {"choices": [{"message": {"annotations": []}}]}),
        (_glm_results, {}),
    ],
)
def test_native_source_parsing_rejects_payloads_without_sources(parse, payload) -> None:
    """🔴 空来源必须报错而不是返回空列表。

    返回空列表会被上层当成"搜到了但没结果"，这条链就停在这一家、不再往下一级来源走 ——
    用户看到的是"没搜到"，实际上是"这家没返回"。报错才能让兜底链继续。
    """
    with pytest.raises(SearchProviderError) as error:
        parse(payload, 5)
    assert error.value.code == "SEARCH_PROVIDER_ERROR"


def test_native_source_results_are_capped_at_max_results() -> None:
    payload = {
        "web_search": [
            {"title": f"t{index}", "link": f"https://e.test/{index}", "content": "c"}
            for index in range(9)
        ]
    }
    assert len(_glm_results(payload, 3).results) == 3


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
