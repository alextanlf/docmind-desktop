"""模型提名：能解析就用，解析不了就当作没提名 —— 绝不猜。"""
from __future__ import annotations

import asyncio

import pytest

from app.core.llm import ChatDelta
from app.core.model_capabilities import TEMPERATURE_MIN_DETERMINISTIC
from app.corpus.nomination import LLMDomainNominator, parse_nominations


class StubLLM:
    def __init__(self, text: str = "", *, error: Exception | None = None, delay: float = 0.0) -> None:
        self.text, self.error, self.delay = text, error, delay
        self.requests = []

    async def stream_chat(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        if self.delay:
            await asyncio.sleep(self.delay)
        yield ChatDelta(content=self.text)


@pytest.mark.asyncio
async def test_valid_json_is_parsed_with_its_reasons() -> None:
    llm = StubLLM(
        '好的：\n```json\n[{"domain": "docs.python.org", "reason": "Python 官方文档"},'
        ' {"domain": "pypi.org", "reason": "包索引"}]\n```'
    )

    found = await LLMDomainNominator(llm).nominate("Python 打包")

    assert [(item.domain, item.reason) for item in found] == [
        ("docs.python.org", "Python 官方文档"),
        ("pypi.org", "包索引"),
    ]


@pytest.mark.asyncio
async def test_bare_string_array_is_accepted_with_a_stated_missing_reason() -> None:
    """模型常常只给域名数组。信息是真的，只是少了理由 —— 丢掉它等于因为格式不合意
    就假装模型没提名；写清"没有说明理由"，用户至少知道缺的是什么。"""
    found = await LLMDomainNominator(StubLLM('["docs.python.org"]')).nominate("Python")

    assert [(item.domain, item.reason) for item in found] == [
        ("docs.python.org", "模型没有说明理由")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    ["", "抱歉，我无法提供域名。", "[]", "[这不是 JSON", '{"domain": "docs.python.org"}'],
)
async def test_unparsable_output_yields_nothing(text: str) -> None:
    assert await LLMDomainNominator(StubLLM(text)).nominate("Python") == []


@pytest.mark.asyncio
async def test_domains_the_model_invents_are_filtered_by_shape() -> None:
    """模型很擅长编出一个看起来很像的域名。形状不对的一律拒掉。"""
    llm = StubLLM(
        '[{"domain": "not a domain", "reason": "编的"},'
        ' {"domain": "127.0.0.1", "reason": "内网"},'
        ' {"domain": "localhost", "reason": "本机"},'
        ' {"domain": "docs.python.org", "reason": "真的"}]'
    )

    found = await LLMDomainNominator(llm).nominate("Python")

    assert [item.domain for item in found] == ["docs.python.org"]


@pytest.mark.asyncio
async def test_duplicates_collapse_and_the_limit_is_respected() -> None:
    llm = StubLLM(
        '[{"domain": "docs.python.org", "reason": "a"},'
        ' {"domain": "DOCS.python.org", "reason": "b"},'
        ' {"domain": "pypi.org", "reason": "c"},'
        ' {"domain": "nodejs.org", "reason": "d"}]'
    )

    found = await LLMDomainNominator(llm).nominate("Python", limit=2)

    assert [item.domain for item in found] == ["docs.python.org", "pypi.org"]


@pytest.mark.asyncio
async def test_provider_failure_yields_no_nominations_instead_of_raising() -> None:
    """提名是锦上添花：让它失败连带把导入打挂是本末倒置。"""
    llm = StubLLM(error=RuntimeError("provider exploded"))

    assert await LLMDomainNominator(llm).nominate("Python") == []


@pytest.mark.asyncio
async def test_slow_provider_times_out_to_no_nominations() -> None:
    llm = StubLLM('[{"domain": "docs.python.org", "reason": "x"}]', delay=0.05)

    assert await LLMDomainNominator(llm, timeout_seconds=0.01).nominate("Python") == []


@pytest.mark.asyncio
async def test_an_empty_topic_never_calls_the_model() -> None:
    llm = StubLLM("[]")

    assert await LLMDomainNominator(llm).nominate("   ") == []
    assert llm.requests == []


@pytest.mark.asyncio
async def test_the_prompt_asks_for_json_and_first_party_domains() -> None:
    """需求写在提示词里，也在测试里 —— 有人把它改成自由文本时，解析器会静默地一条
    都拿不到，而那种失败看起来像"模型没提名"。"""
    llm = StubLLM("[]")
    await LLMDomainNominator(llm).nominate("Python")

    prompt = llm.requests[0].messages[0].content
    assert "JSON" in prompt
    assert "第一方" in prompt
    assert "域名" in prompt
    # 低方差而不是绝对零：有的厂商把 temperature 的合法区间写成 (0, 1]，送 0 会让
    # 整个请求失败。
    assert llm.requests[0].temperature == TEMPERATURE_MIN_DETERMINISTIC


def test_absurdly_long_reasons_are_truncated() -> None:
    found = parse_nominations(
        '[{"domain": "docs.python.org", "reason": "%s"}]' % ("很长的理由" * 100)
    )

    assert len(found[0].reason) == 200
