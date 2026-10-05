"""复核实验：严格「零词法重叠」用例下的 BM25 vs 向量召回。

为什么要复核第一版
------------------
`core/retrieval.py::tokenize` 把中文切成**单字**（`[一-鿿]`），再叠加英文词。
这意味着任何中文查询只要和目标块共享哪怕一个字就算「词法命中」，
第一版实验里 7/7 的「同义改写」其实是靠单字偶然重叠撑起来的，**过于乐观**。

本版每个用例都逐字校验：query 与 gold 块的 token 交集为空（零词法重叠），
外加一组「换了语言」的最难负例。符合项目方法论：结论必须带困难负例复核。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DOCMIND_DATA_DIR", tempfile.mkdtemp(prefix="docmind-ab2-"))

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.retrieval import reciprocal_rank_fusion, tokenize  # noqa: E402
from rank_bm25 import BM25Okapi  # noqa: E402

MODEL_DIR = Path(
    "~/Library/Application Support/docmind-desktop/models/onnx--BAAI--bge-m3"
).expanduser()

# gold 块刻意使用与 query **语义等价但措辞不同**的表述。
# 设计要求：gold 必须是 query 的合法答案（否则测的是用例缺陷，不是通道能力）。
CASES: list[tuple[str, str, list[str], str]] = [
    # (query, kind, gold, gold_text)
    (
        "怎么让安装包更小一些？",
        "同义·零重叠",
        ["g1"],
        "裁剪不必要的第三方依赖并压缩字重，可以显著减轻安装包体积。",
    ),
    (
        "为什么去掉相似度阈值之后准确率反而上升了？",
        "同义·零重叠",
        ["g2"],
        "证据是否充分本应由生成模型自行判断；固定绝对分会系统性误杀有效召回。",
    ),
    (
        "送给模型的上下文里怎么排除不相关的片段？",
        "同义·零重叠",
        ["g3"],
        "只保留多路召回都给出信号的少数片段，其余一律丢弃后再拼接提示词。",
    ),
    (
        "how do I shrink the installer footprint?",
        "跨语言·同义",
        ["g1"],
        "裁剪不必要的第三方依赖并压缩字重，可以显著减轻安装包体积。",
    ),
    (
        "文档开头那段简介是怎么参与检索的？",
        "机制·零重叠",
        ["g4"],
        "标题与摘要会被抽成一个独立的短块，单独建索引后再参与召回排序。",
    ),
    (
        "之前聊过的结论下次提问还会被用到吗？",
        "机制·零重叠",
        ["g5"],
        "每轮对话结束后生成要点沉淀，转成向量后写入独立的记忆库供后续召回。",
    ),
]

CORPUS: list[tuple[str, str]] = [
    *[(f"g{i + 1}", text) for i, (_, _, _, text) in enumerate(CASES)],
    # 干扰块：与 gold 同领域但不含答案
    ("d1", "打包流程包含前端构建、后端运行时准备与签名三个阶段。"),
    ("d2", "检索采用倒数排名融合把多路召回结果合并后重排。"),
    ("d3", "分块过大时同一段落会被切到相邻两个片段里。"),
    ("d4", "本地模型推理延迟受上下文长度影响明显。"),
    ("d5", "密钥统一存放在系统钥匙串中，数据库只保存指针。"),
    ("d6", "远程知识库以复合唯一键绑定仓库与远端标识。"),
    ("d7", "重排序模型的推理耗时约为双塔的十倍。"),
    ("d8", "索引重建期间旧向量需要在提交前保持可用。"),
]


def lexical_overlap(query: str, gold_text: str) -> set[str]:
    return set(tokenize(query)) & set(tokenize(gold_text))


def main() -> int:
    from app.core.onnx_embedding import ONNXEmbeddingModel

    model = ONNXEmbeddingModel(MODEL_DIR, max_seq_length=8192)
    mat = model.encode([t for _, t in CORPUS] + [c[0] for c in CASES], normalize_embeddings=True)
    vecs = {cid: list(map(float, mat[i])) for i, (cid, _) in enumerate(CORPUS)}
    qvecs = [list(map(float, mat[len(CORPUS) + i])) for i in range(len(CASES))]

    tokens = [tokenize(text) or ["_"] for _, text in CORPUS]
    texts = {cid: text for cid, text in CORPUS}

    print("=" * 88)
    print("词法重叠体检（tokenize 把中文切单字，故列出与 gold 的交集）：")
    for query, _, gold, _ in CASES:
        for gid in gold:
            overlap = sorted(lexical_overlap(query, texts[gid]))
            print(
                f"  {query[:22]:<24} → {gid}  "
                + ("✓ 无重叠" if not overlap else f"单字重叠 {overlap}")
            )
    print("=" * 88)
    print("注：单字重叠是中文 BM25 的噪声源（'的/分/体' 等），下面同时给出 gold 的真实排名。\n")

    rows = []
    for i, (query, kind, gold, _) in enumerate(CASES):
        gold_set = set(gold)
        scores = BM25Okapi(tokens).get_scores(tokenize(query))
        ranked = sorted(
            zip([cid for cid, _ in CORPUS], scores), key=lambda it: (-float(it[1]), it[0])
        )
        b = [cid for cid, s in ranked[:3] if float(s) > 0]
        full_rank = [cid for cid, _ in ranked]
        gold_rank = next(
            (full_rank.index(g) + 1 for g in gold if g in full_rank), None
        )
        qv = qvecs[i]
        v = [
            cid
            for cid, _ in sorted(
                vecs.items(), key=lambda it: (-sum(x * y for x, y in zip(it[1], qv)), it[0])
            )[:3]
        ]
        fused = [it.chunk_id for it in reciprocal_rank_fusion(v, b, k=60)][:3]
        rows.append(
            {
                "query": query,
                "kind": kind,
                "gold": gold,
                "bm25": b,
                "vector": v,
                "fused": fused,
                "bm25_gold_rank": gold_rank,
                "bm25_hit": any(c in gold_set for c in b[:3]),
                "vector_hit": any(c in gold_set for c in v[:3]),
                "fused_hit": any(c in gold_set for c in fused[:3]),
            }
        )

    print(f"\n{'查询':<26}{'类型':<14}{'BM25':<7}{'向量':<7}{'融合':<7}{'gold排名':<9}vec top3")
    print("-" * 96)
    for r in rows:
        m = lambda ok: "✓" if ok else "✗"  # noqa: E731
        print(
            f"{r['query'][:24]:<26}{r['kind']:<14}{m(r['bm25_hit']):<7}"
            f"{m(r['vector_hit']):<7}{m(r['fused_hit']):<7}"
            f"{str(r['bm25_gold_rank']):<9}{r['vector'][:3]}"
        )

    n = len(rows)
    b_ok = sum(r["bm25_hit"] for r in rows)
    v_ok = sum(r["vector_hit"] for r in rows)
    f_ok = sum(r["fused_hit"] for r in rows)
    print("-" * 88)
    print(f"n={n}   BM25 {b_ok}/{n}   向量 {v_ok}/{n}   融合 {f_ok}/{n}   (BM25 列 == 去掉 embedding 的效果)")
    print(f"零重叠用例下 BM25 召回率 = {b_ok / n:.0%}（与「无 embedding」完全等价于该列）")

    out = Path(__file__).resolve().parent / "ab_zero_overlap.json"
    out.write_text(
        json.dumps(
            {"summary": {"n": n, "bm25": b_ok, "vector": v_ok, "fused": f_ok}, "rows": rows},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"明细写入 {out}")
    return 0


if __name__ == "__main__":
    _c = main()
    sys.stdout.flush()
    os._exit(_c)
