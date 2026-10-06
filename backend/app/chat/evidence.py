from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceDecision:
    sufficient: bool
    max_similarity: float


def decide_evidence(_query: str, hits: list, judge=None, *, threshold: float = 0.65) -> EvidenceDecision:
    # 🔴 只能读 `vector_score`：RetrievalHit 没有 `similarity` 字段（schemas/retrieval.py）。
    # 原写法 `getattr(hit, "similarity", getattr(hit, "vector_score", 0.0))` 里，
    # 内层 getattr 是**默认参数**，无论外层命中与否都会先求值 —— 于是向量通道
    # 完全没命中时（纯 BM25 命中，vector_score 为 None）拿到 None，
    # float(None) 直接抛 TypeError，把整条 /api/chat/stream 打成 500。
    # 实测：RetrievalHit(..., vector_score=None) -> TypeError。
    # 另一个来源 MemoryHit 有 `similarity` 但没有 `vector_score`，故两个名字都要认。
    def score_of(hit) -> float:
        value = getattr(hit, "vector_score", None)
        if value is None:
            value = getattr(hit, "similarity", None)
        return float(value) if value is not None else 0.0

    score = max((score_of(hit) for hit in hits), default=0.0)
    return EvidenceDecision(sufficient=bool(hits) and score >= threshold, max_similarity=score)
