"""实测：关闭 embedding（纯 BM25）vs 现有双通道，对同一批查询的召回差异。

设计要点
--------
1. 语料刻意混入「同义改写 / 中英交叉 / 术语别名 / 长查询」四类难例 ——
   这些正是纯词法检索的经典失败区。
2. 每个查询标注 gold chunk id，只有 top-3 命中 gold 才算召回成功。
3. 复用生产代码的 tokenize()，不自己另写分词，避免实验与线上不一致。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DOCMIND_DATA_DIR", tempfile.mkdtemp(prefix="docmind-ab-"))

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.retrieval import reciprocal_rank_fusion, tokenize  # noqa: E402
from rank_bm25 import BM25Okapi  # noqa: E402

MODEL_DIR = Path(
    "~/Library/Application Support/docmind-desktop/models/onnx--BAAI--bge-m3"
).expanduser()

# ---------------------------------------------------------------- 语料
# 12 个 chunk，覆盖同义 / 跨语言 / 别名 / 细节数字四类。
CORPUS: list[tuple[str, str]] = [
    ("c01", "本文提出一种基于双塔检索器的文档问答框架，核心是召回后交给大模型判答。"),
    ("c02", "The proposed architecture relies on a bi-encoder retriever followed by a generative decoder."),
    ("c03", "系统的吞吐瓶颈出现在向量召回阶段，单次查询平均耗时 120 毫秒。"),
    ("c04", "实验表明移除 reranker 后准确率不降反升，因为生成模型自带判别能力。"),
    ("c05", "分块策略采用固定窗口 512 字符，重叠 64 字符，实测平均块长 2848 字符。"),
    ("c06", "文档摘要被单独索引为 overview 块，用于回答全局型提问。"),
    ("c07", "The evaluation set contains 33 references from the same research field."),
    ("c08", "记忆模块把会话摘要向量化后单独建库，与正文库物理隔离。"),
    ("c09", "配置项 rag_similarity_threshold 只对记忆召回生效，对正文检索无效。"),
    ("c10", "Spa 赛道的弯心半径与实测中心线数据存在约 0.4 米偏差。"),
    ("c11", "打包产物约 1.4GB，其中后端运行时 569MB，嵌入模型 565MB。"),
    ("c12", "远程知识库通过 provider 抽象接入，平台专属逻辑禁止进入同步层。"),
]

# ---------------------------------------------------------------- 查询
# gold: 期望被召回的 chunk id；kind: 查询类型
QUERIES: list[tuple[str, str, list[str]]] = [
    ("文档问答系统怎么把召回来的内容交给模型判断？", "同义改写", ["c01"]),
    ("哪部分决定了查询速度？", "同义改写", ["c03"]),
    ("去掉重排序之后效果变好了吗？", "术语别名", ["c04"]),
    ("bi-encoder 后面接的是什么模块？", "中英交叉", ["c02"]),
    ("测试集里有多少篇参考文献？", "中英交叉", ["c07"]),
    ("how long is a typical chunk with overlap", "中英交叉", ["c05"]),
    ("窗口大小和重叠长度分别是多少？", "细节数字", ["c05"]),
    ("安装包体积主要由哪几部分组成？", "细节数字", ["c11"]),
    ("记忆内容存在哪里，是跟正文放一起吗？", "同义改写", ["c08"]),
    ("这个阈值设置对正文检索有影响吗？", "同义改写", ["c09"]),
    ("Spa 赛道的几何和实测数据对得上吗？", "同义改写", ["c10"]),
    ("平台相关的代码应该放在哪一层？", "同义改写", ["c12"]),
    ("摘要块是干什么的？", "同义改写", ["c06"]),
    ("总体流程是什么？", "全局型", ["c01", "c02"]),
]


def bm25_rank(query: str, top_k: int = 3) -> list[str]:
    tokens = [tokenize(text) or ["_"] for _, text in CORPUS]
    if not tokenize(query):
        return []
    scores = BM25Okapi(tokens).get_scores(tokenize(query))
    ranked = sorted(
        zip([cid for cid, _ in CORPUS], scores),
        key=lambda item: (-float(item[1]), item[0]),
    )
    return [cid for cid, score in ranked[:top_k] if float(score) > 0]


def vector_rank(vecs: dict[str, list[float]], qvec: list[float], top_k: int = 3) -> list[str]:
    def cos(a: list[float]) -> float:
        return sum(x * y for x, y in zip(a, qvec))

    ranked = sorted(vecs.items(), key=lambda item: (-cos(item[1]), item[0]))
    return [cid for cid, _ in ranked[:top_k]]


def rr_hit(ranked: list[str], gold: set[str]) -> bool:
    return any(cid in gold for cid in ranked[:3])


def main() -> int:
    from app.core.onnx_embedding import ONNXEmbeddingModel

    model = ONNXEmbeddingModel(MODEL_DIR, max_seq_length=8192)
    texts = [text for _, text in CORPUS]
    mat = model.encode(texts + [q for q, _, _ in QUERIES], normalize_embeddings=True)

    vecs = {cid: list(map(float, mat[i])) for i, (cid, _) in enumerate(CORPUS)}
    qvecs = [list(map(float, mat[len(CORPUS) + i])) for i in range(len(QUERIES))]

    rows = []
    for i, (query, kind, gold) in enumerate(QUERIES):
        gold_set = set(gold)
        b = bm25_rank(query)
        v = vector_rank(vecs, qvecs[i])
        fused = [item.chunk_id for item in reciprocal_rank_fusion(v, b, k=60)][:3]
        rows.append(
            {
                "query": query,
                "kind": kind,
                "gold": gold,
                "bm25": b,
                "vector": v,
                "fused": fused,
                "bm25_hit": rr_hit(b, gold_set),
                "vector_hit": rr_hit(v, gold_set),
                "fused_hit": rr_hit(fused, gold_set),
            }
        )

    total = len(rows)
    bm25_ok = sum(r["bm25_hit"] for r in rows)
    vec_ok = sum(r["vector_hit"] for r in rows)
    fus_ok = sum(r["fused_hit"] for r in rows)

    print("=" * 78)
    print(f"{'查询':<26}{'类型':<10}{'BM25':<8}{'向量':<8}{'融合':<8}gold")
    print("=" * 78)
    for r in rows:
        mark = lambda ok: "✓" if ok else "✗"  # noqa: E731
        print(
            f"{r['query'][:24]:<26}{r['kind']:<10}"
            f"{mark(r['bm25_hit']):<8}{mark(r['vector_hit']):<8}{mark(r['fused_hit']):<8}"
            f"{','.join(r['gold'])}  bm25→{r['bm25'][:3]} vec→{r['vector'][:3]}"
        )
    print("=" * 78)
    print(f"n={total}  BM25 {bm25_ok}/{total}  向量 {vec_ok}/{total}  融合 {fus_ok}/{total}")

    by_kind: dict[str, list[int]] = {}
    for r in rows:
        slot = by_kind.setdefault(r["kind"], [0, 0, 0, 0])
        slot[0] += 1
        slot[1] += r["bm25_hit"]
        slot[2] += r["vector_hit"]
        slot[3] += r["fused_hit"]
    print("\n分类型召回率（top-3）：")
    for kind, (n, b, v, f) in sorted(by_kind.items()):
        print(f"  {kind:<10} n={n:<3} BM25 {b}/{n}  向量 {v}/{n}  融合 {f}/{n}")

    only_vec = [r["query"] for r in rows if r["vector_hit"] and not r["bm25_hit"]]
    only_bm = [r["query"] for r in rows if r["bm25_hit"] and not r["vector_hit"]]
    print(f"\n只有向量能召回（BM25 漏）{len(only_vec)} 条：")
    for q in only_vec:
        print("  -", q)
    print(f"只有 BM25 能召回（向量漏）{len(only_bm)} 条：")
    for q in only_bm:
        print("  -", q)

    out = Path(__file__).resolve().parent / "ab_bm25_vs_vector.json"
    out.write_text(
        json.dumps(
            {"summary": {"n": total, "bm25": bm25_ok, "vector": vec_ok, "fused": fus_ok}, "rows": rows},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n明细写入 {out}")
    return 0


if __name__ == "__main__":
    import sys as _sys

    _code = main()
    _sys.stdout.flush()
    import os as _os

    _os._exit(_code)  # 绕开 teardown 崩溃（必须先 flush，否则缓冲区内容丢失）
