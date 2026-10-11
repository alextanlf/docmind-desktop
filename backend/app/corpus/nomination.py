"""让模型凭参数化知识提名第一方来源域名。

🔴 关键认识：**提名不需要联网。** 模型说出「Python 的官方文档在 docs.python.org」
靠的是训练数据，不是搜索。所以这条信号在「仅本地」模式（本地模型）、在 Tavily 免
密钥额度打满、在厂商干脆不支持内置联网时**都还能用** —— 而那正是这个模块要解决的
场景：不能用上游搜索的时候，靠什么决定该抓哪些站点。

代价是它的输出**未经核对**：域名可能搬过家，模型也可能编出一个看起来很像的域名。
因此三条纪律：

1. **解析失败一律当"没提名"，绝不猜。** 与 `LLMQueryPlanner` 同样的降级立场 ——
   提名是锦上添花，让它失败连带把导入打挂是本末倒置。
2. **只有形状合法的域名能进来**（`normalize_domain`），IP、`localhost`、带凭据的
   URL 一律拒掉。
3. **证据里必须写明"未经核对"**，让用户知道这一条跟种子表不是一回事 —— 系统不该
   把自己的猜测显示得像一个已验证的事实。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Protocol

from app.core.llm import ChatRequest, LLMMessage, LLMProvider
from app.core.model_capabilities import TEMPERATURE_MIN_DETERMINISTIC
from app.corpus.candidates import NominatedSource, normalize_domain

logger = logging.getLogger(__name__)

_JSON_ARRAY = re.compile(r"\[.*\]", re.DOTALL)
_MAX_REASON_CHARS = 200
#: 未说明理由时用的占位。留空会让界面上出现一条没有依据的候选，看不出所以然。
_NO_REASON = "模型没有说明理由"

#: 提名的系统提示。**测试会断言它包含 "JSON" 与"第一方"** —— 有人把它改成自由文本
#: 时，解析器会静默地一条都拿不到，而那种失败看起来像"模型没提名"。
_PROMPT = (
    "你是技术资料源规划器。针对下面的主题，列出最多 {limit} 个**第一方**网站域名。\n"
    "要求：\n"
    "1. 只列官方文档、规范、包索引、厂商文档这类一手来源；\n"
    "2. 不要列问答站、内容聚合站、镜像站；\n"
    "3. 只写域名（例如 docs.python.org），不要写完整网址、不要写路径；\n"
    '4. 只输出 JSON 数组，每项形如 {{"domain": "docs.python.org", '
    '"reason": "Python 官方文档"}}，reason 用一句话说明它是什么。\n\n'
    "主题：{topic}"
)


class DomainNominator(Protocol):
    async def nominate(self, topic: str, *, limit: int = 5) -> list[NominatedSource]: ...


class LLMDomainNominator:
    """用聊天模型提名来源域名。任何失败都退化成"没有提名"。"""

    def __init__(self, llm: LLMProvider, *, timeout_seconds: float = 20.0) -> None:
        self.llm = llm
        self.timeout_seconds = timeout_seconds

    async def nominate(self, topic: str, *, limit: int = 5) -> list[NominatedSource]:
        cleaned = " ".join((topic or "").split())
        if not cleaned or limit <= 0:
            return []
        try:
            text = await asyncio.wait_for(
                self._complete(cleaned, limit), self.timeout_seconds
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - 提名失败不该阻断导入
            logger.warning("domain nomination failed: %s", error)
            return []
        return parse_nominations(text, limit=limit)

    async def _complete(self, topic: str, limit: int) -> str:
        request = ChatRequest(
            messages=[
                LLMMessage(role="user", content=_PROMPT.format(topic=topic, limit=limit))
            ],
            # 与查询改写同样的理由：要低方差而不是绝对零 —— 有的厂商把 temperature
            # 的合法区间写成 (0, 1]，送 0 会让整个请求失败。
            temperature=TEMPERATURE_MIN_DETERMINISTIC,
        )
        parts: list[str] = []
        async for delta in self.llm.stream_chat(request):
            parts.append(delta.content)
        return "".join(parts)


def parse_nominations(text: str, *, limit: int = 5) -> list[NominatedSource]:
    """从模型输出里取出域名。取不出来就返回空列表。

    容忍两种形状：带理由的对象数组（提示词要求的），以及裸字符串数组（模型常见的
    简化）。第二种也收，是因为它的信息是真的，只是少了理由 —— 丢掉它等于因为格式
    不合意就假装模型没提名；理由位置写一句"模型没有说明理由"，用户看得见缺的是什么。
    """
    match = _JSON_ARRAY.search(text or "")
    if match is None:
        return []
    try:
        payload = json.loads(match.group(0))
    except ValueError:
        return []
    if not isinstance(payload, list):
        return []

    found: list[NominatedSource] = []
    seen: set[str] = set()
    for item in payload:
        if isinstance(item, str):
            domain, reason = item, _NO_REASON
        elif isinstance(item, dict):
            domain = item.get("domain")
            reason = item.get("reason")
            if not isinstance(domain, str):
                continue
            reason = reason if isinstance(reason, str) and reason.strip() else _NO_REASON
        else:
            continue
        normalized = normalize_domain(domain)
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        found.append(
            NominatedSource(domain=normalized, reason=" ".join(reason.split())[:_MAX_REASON_CHARS])
        )
        if len(found) >= limit:
            break
    return found
