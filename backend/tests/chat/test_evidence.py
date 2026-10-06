from __future__ import annotations

from app.chat.evidence import decide_evidence
from app.memory.retriever import MemoryHit
from app.schemas.retrieval import RetrievalHit


def _hit(**kwargs) -> RetrievalHit:
    base = {
        "chunk_id": "c1",
        "document_id": "d1",
        "document_title": "t",
        "text": "x",
        "fused_score": 0.5,
    }
    return RetrievalHit(**{**base, **kwargs})


def test_pure_bm25_hit_does_not_raise() -> None:
    """纯 BM25 命中（vector_score=None）必须能算出分数，不能抛 TypeError。

    回归：`getattr(hit, "similarity", getattr(hit, "vector_score", 0.0))` 里
    内层 getattr 是默认参数、无条件先求值，拿到 None 后 `float(None)` 抛
    TypeError，整条 /api/chat/stream 会打成 500。
    """
    decision = decide_evidence("q", [_hit(keyword_score=2.0, vector_score=None)])

    assert decision.max_similarity == 0.0
    assert decision.sufficient is False


def test_hybrid_hit_uses_vector_score() -> None:
    decision = decide_evidence("q", [_hit(vector_score=0.9, keyword_score=3.0)])

    assert decision.max_similarity == 0.9
    assert decision.sufficient is True


def test_memory_hit_uses_similarity_field() -> None:
    """MemoryHit 只有 `similarity`、没有 `vector_score`，两个名字都要认。"""
    hit = MemoryHit(
        id="m1",
        kind="session_summary",
        source_id="s1",
        text="x",
        repository_id="r1",
        similarity=0.8,
        metadata={},
    )

    decision = decide_evidence("q", [hit])

    assert decision.max_similarity == 0.8
    assert decision.sufficient is True


def test_empty_hits_is_insufficient() -> None:
    decision = decide_evidence("q", [])

    assert decision.sufficient is False
    assert decision.max_similarity == 0.0