"""复核实验：能不能用一个**相关度判别器**决定"该不该用本地语料作答"。

要同时解决的两件事
------------------
1. 现在的规则是"词法优先、向量救援"，不是真融合：词法弱、语义强的查询拿不到语义那条。
2. 救援模式下，零词面重合而语料其实答不了的问题，会得到语料"最好的猜测"，挡住上游。

而这两件是同一个问题：**只要有办法判断"语料返回的这条够不够格作答"，就可以放心融合**
（不相关的向量结果不会挡住上游）；判断不了，融合就会让语料回答每一个问题。

上次量过什么、缺什么
--------------------
`ab_corpus_language_gap.py` 量出的余弦分布是重叠的（相关最低 0.471，不相关最高 0.566），
但那次全是**同领域**的干扰页（都是软件主题的短文）。这恰好漏掉了最要紧的那一类负例：
**领域内但语料里没有答案**的问题（例如"PostgreSQL 主从复制怎么配"—— 是软件问题，但这
10 页里没有答案）。这类问题如果相似度很高，就说明"够不够格"没法靠分数判断。

所以这个脚本量三类查询、四个判别器：

- 类别 A `in_scope`：语料里有答案（复用上一个脚本的 10 条纯中文问句）。
- 类别 B `out_of_domain`：与语料毫无关系（酸菜、绘画、红酒…）。
- 类别 C `in_domain_unanswerable`：同领域、但语料里没有答案 —— **最难也最要紧的负例**。

判别器：top1 相似度、top1−top2、top1−语料中位数、top1 在该查询分布里的 z 值。
只要有一个判别器能让 A 类与 B∪C 类**完全分开**，门槛就可以定；分不开就不能定。

用法（从 backend/ 运行）::

    .venv/bin/python scripts/ab_corpus_relevance_gate.py
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ab_corpus_language_gap import (  # noqa: E402  (脚本同目录，运行时会自动进 sys.path)
    BACKEND_ROOT,
    CASES,
    CORPUS,
    MODEL_DIR,
    ModelEmbedder,
)

#: 与语料毫无关系的问题。这类问题不该由本地语料作答。
OUT_OF_DOMAIN = [
    "酸菜怎么腌制才脆",
    "文艺复兴时期的绘画有什么特点",
    "感冒发烧应该吃什么药",
    "怎么给猫剪指甲",
    "如何挑选一瓶合适的红酒",
    "北京到上海的高铁要坐多久",
]

#: 同领域但**语料里没有答案**的问题。它们才是"挡住上游"的真实来源：
#: 用户问的是软件问题（看起来该由技术语料回答），而这批页面里确实没有答案。
IN_DOMAIN_UNANSWERABLE = [
    "PostgreSQL 的主从复制怎么配置",
    "Kubernetes 的 HPA 怎么按自定义指标扩缩容",
    "React Server Components 的流式渲染是怎么实现的",
    "Zig 的 comptime 和宏有什么区别",
    "Nginx 的 upstream keepalive 连接池怎么调优",
    "Prometheus 的 recording rule 怎么写",
]


def statistics(query_vector: list[float], vectors: dict[str, list[float]]) -> dict[str, float]:
    scores = sorted(
        (
            (chunk_id, sum(x * y for x, y in zip(vector, query_vector)))
            for chunk_id, vector in vectors.items()
        ),
        key=lambda item: -item[1],
    )
    values = [score for _, score in scores]
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    std = variance**0.5 or 1e-9
    median = values[len(values) // 2]
    return {
        "top1": values[0],
        "top2": values[1] if len(values) > 1 else values[0],
        "median": median,
        "z": (values[0] - mean) / std,
        "margin_top2": values[0] - (values[1] if len(values) > 1 else values[0]),
        "margin_median": values[0] - median,
        "top_id": scores[0][0],  # type: ignore[dict-item]
    }


def main() -> int:
    from app.core.onnx_embedding import ONNXEmbeddingModel

    model = ONNXEmbeddingModel(MODEL_DIR, max_seq_length=8192)
    embedder = ModelEmbedder(model)

    texts = [text for _, text in CORPUS]
    matrix = model.encode(
        texts
        + [query for _, _, query, _ in CASES]
        + OUT_OF_DOMAIN
        + IN_DOMAIN_UNANSWERABLE,
        normalize_embeddings=True,
    )
    chunk_ids = [chunk_id for chunk_id, _ in CORPUS]
    vectors = {chunk_ids[i]: list(map(float, matrix[i])) for i in range(len(CORPUS))}
    offset = len(CORPUS)
    hard = [list(map(float, matrix[offset + i])) for i in range(len(CASES))]
    out_of_domain = [
        list(map(float, matrix[offset + len(CASES) + i])) for i in range(len(OUT_OF_DOMAIN))
    ]
    unanswerable = [
        list(map(float, matrix[offset + len(CASES) + len(OUT_OF_DOMAIN) + i]))
        for i in range(len(IN_DOMAIN_UNANSWERABLE))
    ]

    groups: list[tuple[str, str, dict[str, float]]] = []
    golds = [chunk_id for chunk_id, _, _, _ in CASES]
    for i, (_, _, query, _) in enumerate(CASES):
        stats = statistics(hard[i], vectors)
        stats["hit"] = 1.0 if stats["top_id"] == golds[i] else 0.0  # type: ignore[assignment]
        groups.append(("in_scope", query, stats))
    for i, query in enumerate(OUT_OF_DOMAIN):
        groups.append(("out_of_domain", query, statistics(out_of_domain[i], vectors)))
    for i, query in enumerate(IN_DOMAIN_UNANSWERABLE):
        groups.append(("in_domain_unanswerable", query, statistics(unanswerable[i], vectors)))

    discriminators = ("top1", "margin_top2", "margin_median", "z")
    print("=" * 104)
    print(f"{'类别':<24}{'问题':<30}" + "".join(f"{name:>14}" for name in discriminators))
    for kind, query, stats in groups:
        cells = "".join(f"{stats[name]:>14.4f}" for name in discriminators)
        print(f"{kind:<24}{query[:26]:<30}{cells}")
    print("=" * 104)

    summary: dict[str, dict[str, dict[str, float]]] = {}
    separable: dict[str, bool] = {}
    for name in discriminators:
        by_class = {
            kind: sorted(stats[name] for cls, _, stats in groups if cls == kind)
            for kind in ("in_scope", "out_of_domain", "in_domain_unanswerable")
        }
        summary[name] = {
            kind: {
                "min": round(values[0], 4),
                "median": round(values[len(values) // 2], 4),
                "max": round(values[-1], 4),
            }
            for kind, values in by_class.items()
        }
        # 可分性：存在一个门槛，使 A 类全部通过、而 B∪C 类全部被挡。
        negatives = by_class["out_of_domain"] + by_class["in_domain_unanswerable"]
        separable[name] = min(by_class["in_scope"]) > max(negatives)
        print(
            f"{name:<16} in_scope[{summary[name]['in_scope']['min']:.3f},"
            f"{summary[name]['in_scope']['max']:.3f}]  "
            f"out_of_domain[{summary[name]['out_of_domain']['min']:.3f},"
            f"{summary[name]['out_of_domain']['max']:.3f}]  "
            f"unanswerable[{summary[name]['in_domain_unanswerable']['min']:.3f},"
            f"{summary[name]['in_domain_unanswerable']['max']:.3f}]  "
            + ("✓ 可分" if separable[name] else "✗ 重叠，无法定门槛")
        )

    payload = {
        "summary": summary,
        "separable": separable,
        "rows": [
            {"kind": kind, "query": query, **{k: round(v, 4) for k, v in stats.items() if k != "top_id"}}
            for kind, query, stats in groups
        ],
    }
    out = BACKEND_ROOT / "scripts" / "ab_corpus_relevance_gate.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n结果已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
