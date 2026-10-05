"""三模型对照：bge-m3(1024维/565MB) vs bge-small-zh-v1.5(512维/22MB) vs multilingual-e5-small(384维/112MB)。

回答的问题：DocMind 能不能用小模型换掉 bge-m3，省下 543MB 安装包体积？

关键约束（必须一起报，不能只报召回率）
------------------------------------------------
1. **维度不同 → 必须重建全部索引**。1024 → 512/384 是不同向量空间，
   存量 Chroma 集合与查询向量不同源，cos 不可比。项目已有实测：
   fp32 存量 vs int8 查询 cos≈0.987，换模型会更低。
2. **e5 系列必须加前缀**：`query: ` / `passage: `，漏了直接崩。
   本脚本按官方要求加，作为「正确用法」的成绩。
3. **bge-small-zh 是纯中文模型**，跨语言能力预期显著弱于 bge-m3
   （bge-m3 的卖点就是多语言）。语料必须含跨语言用例才能测出来。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("DOCMIND_DATA_DIR", tempfile.mkdtemp(prefix="docmind-m3bench-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.onnx_embedding import ONNXEmbeddingModel  # noqa: E402
from app.core.retrieval import tokenize  # noqa: E402
from rank_bm25 import BM25Okapi  # noqa: E402

M3_DIR = Path(
    "~/Library/Application Support/docmind-desktop/models/onnx--BAAI--bge-m3"
).expanduser()
SMALL_DIR = Path("/tmp/emb_bench")
E5_DIR = Path("/tmp/emb_e5")

# 语料：中英混排，含同义改写 / 跨语言 / 术语别名 / 机制描述
CORPUS: list[tuple[str, str]] = [
    ("c01", "本文提出一种基于双塔检索器的文档问答框架，核心是召回后交给大模型判答。"),
    ("c02", "The proposed architecture relies on a bi-encoder retriever followed by a generative decoder."),
    ("c03", "系统的吞吐瓶颈出现在向量召回阶段，单次查询平均耗时 120 毫秒。"),
    ("c04", "实验表明移除 reranker 后准确率不降反升，因为生成模型自带判别能力。"),
    ("c05", "分块策略采用固定窗口 512 字符，重叠 64 字符。"),
    ("c06", "文档摘要被单独索引为 overview 块，用于回答全局型提问。"),
    ("c07", "The evaluation set contains 33 references from the same research field."),
    ("c08", "记忆模块把会话摘要向量化后单独建库，与正文库物理隔离。"),
    ("c09", "配置项 rag_similarity_threshold 只对记忆召回生效，对正文检索无效。"),
    ("c10", "Spa 赛道的弯心半径与实测中心线数据存在约 0.4 米偏差。"),
    ("c11", "打包产物约 1.4GB，其中后端运行时 569MB，嵌入模型 565MB。"),
    ("c12", "远程知识库通过 provider 抽象接入，平台专属逻辑禁止进入同步层。"),
    ("c13", "倒排索引先按词条定位候选集合，再按词频与文档长度计算相关性得分。"),
    ("c14", "A sparse lexical index answers exact-match questions better than dense vectors."),
    ("c15", "固定绝对分数阈值会系统性误杀短查询与跨语言查询的有效召回。"),
]

# (query, kind, gold)
QUERIES: list[tuple[str, str, list[str]]] = [
    ("文档问答系统怎么把召回来的内容交给模型判断？", "同义改写", ["c01"]),
    ("哪部分决定了查询速度？", "同义改写", ["c03"]),
    ("去掉重排序之后效果变好了吗？", "术语别名", ["c04"]),
    ("bi-encoder 后面接的是什么模块？", "中英交叉", ["c02"]),
    ("测试集里有多少篇参考文献？", "中英交叉", ["c07"]),
    ("how long is a typical chunk with overlap", "纯英文查询", ["c05"]),
    ("精确匹配类问题哪种索引更合适？", "同义改写", ["c14"]),
    ("词条定位和相关性打分是怎么做的？", "同义改写", ["c13"]),
    ("阈值会不会把有效结果误杀？", "同义改写", ["c15"]),
    ("安装包体积主要由哪几部分组成？", "细节数字", ["c11"]),
    ("记忆内容存在哪里，是跟正文放一起吗？", "同义改写", ["c08"]),
    ("摘要块是干什么的？", "同义改写", ["c06"]),
    ("how is memory stored separately from the main index?", "纯英文查询", ["c08"]),
    ("Spa 赛道的几何和实测数据对得上吗？", "同义改写", ["c10"]),
    ("平台相关的代码应该放在哪一层？", "同义改写", ["c12"]),
    ("总体流程是什么？", "全局型", ["c01", "c02"]),
]

MODELS = [
    ("bge-m3 (当前)", M3_DIR, None, 565, "bge-m3"),
    ("bge-small-zh-v1.5", SMALL_DIR, None, 23, "bge-small-zh-v1.5"),
    ("multilingual-e5-small", E5_DIR, "e5", 135, "multilingual-e5-small"),
]


def dir_mb(path: Path) -> float:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) / 1024 / 1024


def embed(model, texts, kind: str, is_query: bool):
    if kind == "e5":
        prefix = "query: " if is_query else "passage: "
        texts = [prefix + t for t in texts]
    return [list(map(float, v)) for v in encode_compat(model, texts)]


def encode_compat(model, texts: list[str]):
    """绕过 token_type_ids 缺口。

    实测发现（2026-10-05）：`intfloat/multilingual-e5-small` 的量化 ONNX 图把
    `token_type_ids` 声明为**必填输入**，但配套 XLMRoberta tokenizer 默认
    **不产出**该键 → `app/core/onnx_embedding.py:45-49` 的
    `if name in self._input_names and name in encoded` 条件不成立，该输入被漏掉，
    onnxruntime 直接抛 `ValueError: Required inputs (['token_type_ids']) are missing`。

    这意味着 e5 系列无法直接塞进现有 ONNXEmbeddingModel，要么改生产代码补零向量，
    要么换模型。BERT 架构的 bge-* 全部是单句输入，token_type_ids 恒为全 0，
    补零在语义上安全。
    """
    import numpy as np
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(str(model.model_dir), local_files_only=True)
    batches = []
    for start in range(0, len(texts), model.batch_size):
        batch = [str(t) for t in texts[start : start + model.batch_size]]
        enc = tokenizer(
            batch, padding=True, truncation=True,
            max_length=model.max_seq_length, return_tensors="np",
        )
        feed = {
            n: enc[n] for n in ("input_ids", "attention_mask", "token_type_ids")
            if n in model._input_names and n in enc
        }
        for required in model._input_names:
            if required not in feed:
                feed[required] = np.zeros_like(enc["input_ids"])  # 补零兜底
        out = model.session.run(None, feed)
        vec = np.asarray(out[0])
        if vec.ndim == 3:
            vec = vec[:, 0]
        batches.append(vec)
    merged = np.concatenate(batches, axis=0)
    if merged.ndim != 2:
        raise ValueError(f"unexpected output shape: {merged.shape}")
    merged = merged / np.clip(np.linalg.norm(merged, axis=1, keepdims=True), 1e-12, None)
    return merged


def top3(vecs: dict[str, list[float]], qv: list[float]) -> list[str]:
    def cos(a: list[float]) -> float:
        return sum(x * y for x, y in zip(a, qv))

    return [cid for cid, _ in sorted(vecs.items(), key=lambda it: (-cos(it[1]), it[0]))[:3]]


def main() -> int:
    tokens = [tokenize(t) or ["_"] for _, t in CORPUS]
    bm25_scores = {}
    for q, _, _ in QUERIES:
        s = BM25Okapi(tokens).get_scores(tokenize(q))
        bm25_scores[q] = [cid for cid, sc in sorted(
            zip([c for c, _ in CORPUS], s), key=lambda it: (-float(it[1]), it[0])
        )[:3] if float(sc) > 0]

    report: dict[str, dict] = {}
    for name, path, kind, claimed_mb, repo in MODELS:
        if not path.is_dir():
            print(f"跳过 {name}（目录不存在 {path}）")
            continue
        actual_mb = dir_mb(path)
        print(f"\n加载 {name} … 实测目录 {actual_mb:.0f}MB")
        t0 = time.perf_counter()
        model = ONNXEmbeddingModel(path, max_seq_length=2048)
        all_texts = [t for _, t in CORPUS] + [q for q, _, _ in QUERIES]
        vecs = embed(model, all_texts, kind, False)
        dim = len(vecs[0])
        encode_s = time.perf_counter() - t0
        cvecs = {cid: v for (cid, _), v in zip(CORPUS, vecs)}
        qvecs = vecs[len(CORPUS):]

        rows = []
        for i, (q, qkind, gold) in enumerate(QUERIES):
            gs = set(gold)
            v = top3(cvecs, qvecs[i])
            b = bm25_scores[q]
            full = [cid for cid, _ in sorted(
                cvecs.items(), key=lambda it: (-sum(x * y for x, y in zip(it[1], qvecs[i])), it[0])
            )]
            rows.append({
                "query": q, "kind": qkind, "gold": gold,
                "vector": v, "bm25": b,
                "vector_hit": any(c in gs for c in v),
                "bm25_hit": any(c in gs for c in b[:3]),
                "gold_rank": next((full.index(g) + 1 for g in gold if g in full), None),
            })

        n = len(rows)
        report[name] = {
            "repo": repo, "dim": dim, "disk_mb": round(actual_mb, 1),
            "claimed_mb": claimed_mb, "encode_s": round(encode_s, 2),
            "vector_hit": sum(r["vector_hit"] for r in rows),
            "bm25_hit": sum(r["bm25_hit"] for r in rows),
            "n": n, "rows": rows,
        }
        print(f"  维度={dim}  向量召回={report[name]['vector_hit']}/{n}  "
              f"BM25 召回={report[name]['bm25_hit']}/{n}  编码耗时={encode_s:.2f}s")

    # 汇总表
    # 安装包 = 非模型部分(1400 - 565) + 新模型体积
    BASE_WITHOUT_MODEL_MB = 1400 - 565
    print("\n" + "=" * 92)
    print(f"{'模型':<26}{'维度':<7}{'体积':<10}{'向量召回':<11}{'BM25':<9}{'安装包':<22}{'节省'}")
    print("-" * 92)
    for name, r in report.items():
        pkg = BASE_WITHOUT_MODEL_MB + r["disk_mb"]
        print(f"{name:<26}{r['dim']:<7}{r['disk_mb']:.0f}MB{'':<3}"
              f"{str(r['vector_hit']) + '/' + str(r['n']):<11}"
              f"{str(r['bm25_hit']) + '/' + str(r['n']):<9}"
              f"1.4GB → {pkg:.0f}MB{'':<8}-{1400 - pkg:.0f}MB")
    print("=" * 92)

    # 逐模型看漏掉了什么
    for name, r in report.items():
        missed = [x["query"] for x in r["rows"] if not x["vector_hit"]]
        print(f"\n{name} 漏召 {len(missed)} 条：")
        for q in missed:
            print("   -", q)

    # 分类型
    print("\n分类型向量召回：")
    kinds = sorted({q[1] for q in QUERIES})
    print(f"{'类型':<14}" + "".join(f"{n[:20]:<24}" for n in report))
    for k in kinds:
        line = f"{k:<14}"
        for name, r in report.items():
            sub = [x for x in r["rows"] if x["kind"] == k]
            line += f"{str(sum(x['vector_hit'] for x in sub)) + '/' + str(len(sub)):<24}"
        print(line)

    out = Path(__file__).resolve().parent / "ab_small_models.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n明细写入 {out}")
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
