"""联网是否提升文档问答准确性——A/B/C/D 对照实验。

设计原则（照本项目 scripts/calibrate_evidence_gate.py 的方法论）：
1. **prompt 用生产代码构造**，不手写近似：A 用 `build_rag_prompt`，B 用
   `_history_with_prompt`（即线上真实装配）。C/D 只在 B 的输出上做**单点字符串手术**，
   保证与 B 只差一个变量。
2. **负例取"同领域但文档没覆盖/文档写错"**，而不是随便挑不相干的问题 —— 后者不构成判据压力。
3. 三组问题分别测三件事：G1 文档可答（网页是噪声）→ 测**干扰**；
   G2 文档写错而公开事实相反 → 测**能否纠正**；G3 文档完全未覆盖 → 测**拒答行为**。

四个条件：
  A  仅文档（build_rag_prompt）
  B  文档 + 真实 Tavily 网页 = 现状生产 prompt（指令未列 [W#]、问题在中间、网页在问题之后）
  C  B + 修正指令（[W#] 列入允许引用、允许网页补足、文档与网页都不足以才拒答）
  D  C + 把「问题」移到最末（网页证据在问题之前）

用真实 DeepSeek + 真实 Tavily(keyless) 跑，结果写 JSON，原文一并保留供人工复核。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
os.environ.setdefault("DOCMIND_DATA_DIR", "/tmp/docmind-ab-web")
sys.path.insert(0, str(BACKEND))

from app.chat.prompts import build_rag_prompt
from app.chat.service import _history_with_prompt
from app.core.llm import (
    ChatRequest,
    LLMMessage,
    ModelConfig,
    OpenAICompatibleProvider,
)
from app.schemas.retrieval import RetrievalHit
from app.schemas.web_search import SearchRequest
from app.search.tavily import TavilyProvider

MODEL = ModelConfig(
    preset="deepseek",
    base_url="https://api.deepseek.com/v1",
    model="deepseek-flash",
    timeout_seconds=60,
)

DOC = "DocMind 内部部署手册 v2.4"


def hit(cid: str, text: str) -> RetrievalHit:
    return RetrievalHit(
        chunk_id=cid,
        document_id="doc-1",
        document_title=DOC,
        text=text,
        section_path=None,
        page_number=None,
        fused_score=0.9,
    )


# ── 三个场景共用的文档块 ──────────────────────────────────────────────────────
DOC_INTERNAL = [
    hit("c1", "本地后端固定监听 127.0.0.1:18800，不接受绑定到外部网卡。"),
    hit("c2", "导入文档时会同时保留 staging 与 documents 两份副本，单文件上限 100MB。"),
    hit("c3", "本地会话令牌在应用启动时随机生成，写入 userData，不随会话刷新。"),
    hit("c4", "向量库已从 ChromaDB 迁移到 sqlite-vec，旧索引必须重建才能检索。"),
    hit("c5", "检索采用双塔召回 top-3 后直接送模型，不引入 reranker 与相似度阈值。"),
]
DOC_WRONG = [
    hit("c1", "嵌入模型 bge-m3 输出 768 维向量，建表时按此维度声明。"),
    hit("c2", "解析 PDF 使用 PyMuPDF，代码里统一写 `import fitz`。"),
    hit("c3", "Tavily 搜索必须先在官网注册并配置 API Key 才可调用。"),
    hit("c4", "本地后端固定监听 127.0.0.1:18800。"),
]

# ── 问题集 ────────────────────────────────────────────────────────────────────
CASES = [
    # G1 文档可答：答案只存在于内部手册，公开网页不可能有 → 测网页是否造成干扰
    dict(g="G1 文档可答", q="本地后端监听哪个端口？", hits=DOC_INTERNAL,
         right=["18800"], wrong=["18900", "8000", "3000", "8080"]),
    dict(g="G1 文档可答", q="单文件导入上限是多少？", hits=DOC_INTERNAL,
         right=["100MB", "100 MB", "100兆", "100 兆"], wrong=["50MB", "200MB", "1GB", "10GB"]),
    dict(g="G1 文档可答", q="本地会话令牌是怎么生成的？", hits=DOC_INTERNAL,
         right=["启动时", "启动"], wrong=["登录时", "OAuth", "JWT"]),

    # G2 文档写错、公开事实相反 → 测联网能否纠正
    dict(g="G2 文档有误", q="bge-m3 的向量维度是多少？", hits=DOC_WRONG,
         right=["1024"], wrong=["768"]),
    dict(g="G2 文档有误", q="PyMuPDF 现在推荐的导入名是什么？", hits=DOC_WRONG,
         right=["pymupdf"], wrong=["fitz"]),
    dict(g="G2 文档有误", q="使用 Tavily 搜索是否必须先注册 API Key？", hits=DOC_WRONG,
         right=["keyless", "不需要", "无需", "免密钥"], wrong=["必须先注册", "必须注册", "需要注册"]),

    # G3 文档完全未覆盖、同领域 → 测拒答行为
    dict(g="G3 文档未覆盖", q="sqlite-vec 支持哪些向量距离度量？", hits=DOC_INTERNAL,
         right=["cosine", "l2", "欧", "L1", "曼哈顿"], wrong=[]),
    dict(g="G3 文档未覆盖", q="bge-m3 支持的最大输入长度是多少 token？", hits=DOC_INTERNAL,
         right=["8192"], wrong=[]),
    dict(g="G3 文档未覆盖", q="PyMuPDF 采用什么开源许可证？", hits=DOC_INTERNAL,
         right=["AGPL"], wrong=[]),
]

def build_variants(q: str, hits: list[RetrievalHit], web: list) -> dict[str, str]:
    """A = 仅文档（基线）；P = 生产装配（`_history_with_prompt` 原样调用）。

    P 就是线上真实路径，所以这个脚本跑绿 = 生产路径本身被验证，不是"近似实现"。
    """
    a = build_rag_prompt(q, hits)
    p = _history_with_prompt([], "none", q, hits, None, web)[0].content

    # 证明改动真的生效：装配出来的必须是指令的"带网页"版本，否则结论没意义。
    assert "[W#]" in p and "以网页证据为准" in p, "生产 prompt 未使用带网页的裁决指令"
    assert "仅依据提供的文档片段" not in p, "生产 prompt 仍在说『仅依据文档片段』"
    assert p.rstrip().endswith(f"问题：{q}"), "问题不在最末"

    return {"A 仅文档": a, "P 生产（改后）": p}


CITE = re.compile(r"\[([SWM])(\d+)\]")


async def answer(prompt: str) -> str:
    provider = OpenAICompatibleProvider(MODEL, API_KEY)
    chunks = []
    async for delta in provider.stream_chat(
        ChatRequest(messages=[LLMMessage(role="user", content=prompt)], temperature=0.0)
    ):
        if delta.content:
            chunks.append(delta.content)
    return "".join(chunks).strip()


API_KEY = ""


async def main() -> None:
    global API_KEY
    API_KEY = sys.argv[1] if len(sys.argv) > 1 else ""
    if not API_KEY:
        raise SystemExit("usage: scripts/ab_web_vs_local.py <deepseek-key>")

    tavily = TavilyProvider()
    out = []
    for case in CASES:
        t0 = time.time()
        resp = await tavily.search(SearchRequest(query=case["q"], max_results=5))
        web = resp.results
        web_chars = sum(len(r.content) for r in web)
        variants = build_variants(case["q"], case["hits"], web)
        row = {
            "group": case["g"],
            "query": case["q"],
            "web_n": len(web),
            "web_chars": web_chars,
            "web_top": [str(r.canonical_url)[:70] for r in web[:3]],
            "variants": {},
        }
        print(f"\n=== [{case['g']}] {case['q']}")
        print(f"    Tavily {len(web)} 条 / {web_chars} 字符 / {time.time()-t0:.1f}s")
        for name, prompt in variants.items():
            try:
                text = await answer(prompt)
            except Exception as exc:  # noqa: BLE001
                text = f"<ERROR {type(exc).__name__}: {exc}>"
            cites = CITE.findall(text)
            hit_right = [k for k in case["right"] if k in text]
            hit_wrong = [k for k in case["wrong"] if k in text]
            row["variants"][name] = {
                "answer": text,
                "right_hit": hit_right,
                "wrong_hit": hit_wrong,
                "cites": sorted({f"[{a}{b}]" for a, b in cites}),
                "prompt_chars": len(prompt),
            }
            flag = "R" if hit_right else ("W" if hit_wrong else "-")
            print(
                f"    {name:22s} {flag}  right={hit_right} wrong={hit_wrong} "
                f"cites={sorted({f'[{a}{b}]' for a, b in cites})} len={len(text)}"
            )
        out.append(row)
        for name, v in row["variants"].items():
            print(f"      [{name}] {v['answer'].replace(chr(10), ' ')[:230]}")

    out_path = Path(__file__).with_suffix(".json")
    out_path.write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n结果已写入 {out_path}")


asyncio.run(main())
