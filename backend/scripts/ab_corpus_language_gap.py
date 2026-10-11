"""复核实验：本地网页语料上，纯中文问句对英文正文的召回（BM25 vs 向量 vs 融合）。

要回答的问题
------------
本地语料现在是 **BM25 单通道**（`search/web_corpus.py`，理由写在模块注释里：不引入
向量以免把"抓一个站"变成"先下载 500MB 模型"）。但那条理由在实测前就失效了一半：
嵌入模型 bge-m3 **已经随应用在本地**（`models/onnx--BAAI--bge-m3`），而且是多语言的。

而语料与问句的语言往往不同：文档是英文的，问的人用中文。`tokenize` 把中文切单字、
英文切成词，所以**纯中文问句对纯英文正文的词法交集必然为空** —— 这不是"相关度低"，
是"结构上不可能命中"。本脚本量化这条差距，并给出"如果加向量能好多少"。

方法论（照 `ab_zero_overlap.py` 的教训）
--------------------------------------
那份实验记录过一次失误：第一版"同义改写"用例里有 7/7 命中，后来发现是中文切单字
造成的**偶然重合**，过于乐观。所以本脚本：

- 严格组（hard）用**纯中文问句 × 纯英文正文**，交集为空是构造保证，脚本仍逐条打印；
- 对照的上界（easy）是"问句里带上文档用的那个英文术语" —— 这正是 LLM 改写能做到的
  最好情况（`SearchService` 会用 query planner 生成英文变体再喂给语料这一级）；
- 三条通道在同一份语料、同一组问句上比较，避免"各自用有利用例"。

用法（从 backend/ 运行）::

    .venv/bin/python scripts/ab_corpus_language_gap.py

结果同时写入同目录的 `ab_corpus_language_gap.json`，与其它 ab_*.json 一样作为证据保留。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DOCMIND_DATA_DIR", tempfile.mkdtemp(prefix="docmind-corpus-ab-"))

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.onnx_embedding import ONNXEmbeddingModel  # noqa: E402
from app.core.retrieval import reciprocal_rank_fusion, tokenize  # noqa: E402
from app.schemas.web_search import SearchRequest  # noqa: E402
from app.search.web_corpus import WebCorpusSearchProvider  # noqa: E402
from app.storage.database import Database  # noqa: E402
from app.storage.models import WebCorpusChunkRecord, WebCorpusPageRecord, new_id  # noqa: E402
from app.storage.repositories import WebCorpusStore  # noqa: E402

MODEL_DIR = Path(
    "~/Library/Application Support/docmind-desktop/models/onnx--BAAI--bge-m3"
).expanduser()

#: (页面 id, 英文正文, 纯中文问句, 问句的英文术语版本)
#:
#: 中文问句刻意只描述**意图**、不含任何英文术语 —— 这就是"用户用母语提问"的样子。
CASES: list[tuple[str, str, str, str]] = [
    (
        "g1",
        "asyncio event loop scheduling and task cancellation explained for concurrent workers.",
        "事件循环怎么调度并发任务，取消是怎么生效的？",
        "asyncio event loop 调度与 task cancellation",
    ),
    (
        "g2",
        "SQLite write ahead logging mode improves concurrent read performance under load.",
        "数据库怎么在并发读的时候不掉性能？",
        "SQLite write ahead logging 并发读性能",
    ),
    (
        "g3",
        "HTTP cache control headers decide freshness and revalidation for stored responses.",
        "缓存过期时间是由什么决定的，怎么判断要不要重新请求？",
        "HTTP cache control headers freshness revalidation",
    ),
    (
        "g4",
        "React hooks must be called in the same order on every render without conditions.",
        "为什么钩子不能写在条件分支里？",
        "React hooks 调用顺序 条件分支",
    ),
    (
        "g5",
        "Docker image layer caching speeds rebuilds when instructions stay unchanged.",
        "为什么改一行代码之后重新打包还是很慢？",
        "Docker image layer caching rebuild",
    ),
    (
        "g6",
        "Kubernetes liveness probe restarts containers that stop responding to health checks.",
        "容器卡住了怎么让它自动重启？",
        "Kubernetes liveness probe 健康检查 重启",
    ),
    (
        "g7",
        "Git rebase rewrites commit history while merge preserves it with a merge commit.",
        "哪个操作会改写提交历史？",
        "Git rebase 改写 提交历史 merge",
    ),
    (
        "g8",
        "TLS certificate chain validation fails when an intermediate certificate is missing.",
        "证书校验失败通常是缺了什么？",
        "TLS certificate chain 校验 中间证书",
    ),
    (
        "g9",
        "BM25 ranking weighs term frequency against document length for lexical retrieval.",
        "词法检索里的排序是怎么算的？",
        "BM25 ranking term frequency document length",
    ),
    (
        "g10",
        "Electron IPC sends serializable messages between the main and renderer processes.",
        "主进程和界面之间怎么传数据？",
        "Electron IPC main renderer 进程通信",
    ),
]

#: 干扰页：同领域但不含答案。没有它们，任何"返回第一页"的实现都会看起来很好。
DISTRACTORS: list[tuple[str, str]] = [
    ("d1", "The build pipeline runs linting, type checking and packaging in sequence."),
    ("d2", "Retrieval fuses multiple channels with reciprocal rank fusion before reranking."),
    ("d3", "Chunk overlap keeps sentences from being cut across two adjacent fragments."),
    ("d4", "Local model latency grows with the length of the assembled context window."),
    ("d5", "Secrets live in the platform keychain and the database stores only a pointer."),
    ("d6", "Remote knowledge bases bind a repository to a provider container identifier."),
]

CORPUS: list[tuple[str, str]] = [
    *[(cid, text) for cid, text, _, _ in CASES],
    *DISTRACTORS,
]


def seed_store() -> WebCorpusStore:
    """把这份语料写进一个临时库，然后用**真实的**检索源去查它。

    不重新实现 BM25：要量的就是产品里那条路径。
    """
    database = Database(f"sqlite+pysqlite:///{Path(tempfile.mkdtemp()) / 'corpus-ab.sqlite3'}")
    database.upgrade()
    store = WebCorpusStore(database)
    pages, chunks = [], []
    for cid, text in CORPUS:
        page_id = new_id()
        pages.append(
            WebCorpusPageRecord(
                id=page_id,
                domain="docs.test",
                url=f"https://docs.test/{cid}",
                title=text[:40],
                content_hash=cid,
                byte_size=len(text),
            )
        )
        chunks.append(
            WebCorpusChunkRecord(
                page_id=page_id, domain="docs.test", url=f"https://docs.test/{cid}", title=text[:40], text=text
            )
        )
    store.replace_domain("docs.test", pages, chunks)
    return store


def page_of(url: str) -> str:
    return url.rsplit("/", 1)[-1]


def main() -> int:


    store = seed_store()
    provider = WebCorpusSearchProvider(store)

    model = ONNXEmbeddingModel(MODEL_DIR, max_seq_length=8192)
    matrix = model.encode(
        [text for _, text in CORPUS] + [q for _, _, q, _ in CASES] + [e for _, _, _, e in CASES],
        normalize_embeddings=True,
    )
    chunk_ids = [cid for cid, _ in CORPUS]
    offset = len(CORPUS)
    hard_vecs = {chunk_ids[i]: list(map(float, matrix[i])) for i in range(len(CORPUS))}
    q_vecs = [list(map(float, matrix[offset + i])) for i in range(len(CASES))]
    easy_vecs = [list(map(float, matrix[offset + len(CASES) + i])) for i in range(len(CASES))]

    print("=" * 96)
    print("词法重叠体检（纯中文问句 × 纯英文正文）：")
    structural = True
    for cid, text, query, _ in CASES:
        overlap = sorted(set(tokenize(query)) & set(tokenize(text)))
        if overlap:
            structural = False
        print(f"  {query[:26]:<28} → {cid}  " + ("✓ 交集为空" if not overlap else f"✗ {overlap}"))
    print("=" * 96)

    def bm25_ranking(query: str, top: int = 3) -> list[str]:
        response = asyncio.run(provider.search(SearchRequest(query=query, max_results=top)))
        return [page_of(str(result.canonical_url)) for result in response.results]

    def bm25_ranking_safe(query: str, top: int = 3) -> list[str]:
        try:
            return bm25_ranking(query, top)
        except Exception:  # 没命中时 provider 会抛错 —— 那正是"零召回"
            return []

    def vector_ranking(vector: list[float], top: int = 3) -> list[str]:
        scored = sorted(
            hard_vecs.items(),
            key=lambda item: (-sum(x * y for x, y in zip(item[1], vector)), item[0]),
        )
        return [cid for cid, _ in scored[:top]]

    rows = []
    for i, (gold, _text, hard_query, easy_query) in enumerate(CASES):
        hard_bm25 = bm25_ranking_safe(hard_query)
        easy_bm25 = bm25_ranking_safe(easy_query)
        hard_vector = vector_ranking(q_vecs[i])
        easy_vector = vector_ranking(easy_vecs[i])
        fused = [
            item.chunk_id
            for item in reciprocal_rank_fusion(hard_vector, hard_bm25, k=60)
        ][:3]
        rows.append(
            {
                "gold": gold,
                "hard_query": hard_query,
                "easy_query": easy_query,
                "bm25_hard": hard_bm25,
                "bm25_easy": easy_bm25,
                "vector_hard": hard_vector,
                "vector_easy": easy_vector,
                "fused_hard": fused,
                "bm25_hard_hit": gold in hard_bm25,
                "bm25_easy_hit": gold in easy_bm25,
                "vector_hard_hit": gold in hard_vector,
                "vector_easy_hit": gold in easy_vector,
                "fused_hard_hit": gold in fused,
            }
        )

    def rate(key: str) -> str:
        hits = sum(1 for row in rows if row[key])
        return f"{hits}/{len(rows)}"

    summary = {
        "n": len(rows),
        "bm25_hard": rate("bm25_hard_hit"),
        "bm25_easy": rate("bm25_easy_hit"),
        "vector_hard": rate("vector_hard_hit"),
        "vector_easy": rate("vector_easy_hit"),
        "fused_hard": rate("fused_hard_hit"),
        "structural_zero_overlap": structural,
    }
    print("命中率（top-3，gold 页在结果里即算命中）：")
    print(f"  BM25 · 纯中文问句（真实场景）      {summary['bm25_hard']}")
    print(f"  BM25 · 带英文术语（改写完美时）    {summary['bm25_easy']}")
    print(f"  向量 · 纯中文问句                  {summary['vector_hard']}")
    print(f"  向量 · 带英文术语                  {summary['vector_easy']}")
    print(f"  融合 · 纯中文问句                  {summary['fused_hard']}")
    print("=" * 96)
    for row in rows:
        marks = "".join(
            "✓" if row[key] else "·"
            for key in ("bm25_hard_hit", "bm25_easy_hit", "vector_hard_hit", "fused_hard_hit")
        )
        print(f"  {marks}  {row['hard_query'][:30]:<32} gold={row['gold']}")

    payload = {"summary": summary, "rows": rows}
    out = BACKEND_ROOT / "scripts" / "ab_corpus_language_gap.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n结果已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
