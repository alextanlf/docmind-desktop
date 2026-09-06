from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceDecision:
    sufficient: bool
    max_similarity: float


def decide_evidence(_query: str, hits: list, judge=None, *, threshold: float = 0.65) -> EvidenceDecision:
    score = max((float(getattr(hit, "similarity", getattr(hit, "vector_score", 0.0))) for hit in hits), default=0.0)
    return EvidenceDecision(sufficient=bool(hits) and score >= threshold, max_similarity=score)
