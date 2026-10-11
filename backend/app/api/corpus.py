from __future__ import annotations

from fastapi import APIRouter, Request

from app.corpus.candidates import NominatedSource, derive_candidates
from app.corpus.nomination import DomainNominator
from app.schemas.corpus import (
    CorpusCandidateRequest,
    CorpusCandidatesResponse,
    CorpusCandidateView,
    CorpusNominationView,
)

router = APIRouter(prefix="/api/corpus", tags=["corpus"])


@router.post("/candidates", response_model=CorpusCandidatesResponse)
async def derive_source_candidates(
    request: Request, body: CorpusCandidateRequest
) -> CorpusCandidatesResponse:
    """该抓哪些站点：按可信度排序，每条都带证据。

    **提名失败不影响结果。** 模型不能用、超时、输出解析不出来时，候选仍然来自种子表
    与用户显式指定 —— 这不是容错，而是这个功能的前提：它要解决的正是"模型不能联网"
    的场景，所以绝不能把模型当必要条件。
    """
    nominator: DomainNominator | None = getattr(request.app.state, "domain_nominator", None)
    attempted = bool(body.nominate and body.text and nominator is not None)
    nominated: list[NominatedSource] = []
    if attempted and nominator is not None and body.text is not None:
        nominated = await nominator.nominate(body.text, limit=body.nominate_limit)

    candidates = derive_candidates(
        explicit=body.explicit_domains,
        text=body.text,
        nominated=nominated,
        document_urls=body.document_urls,
        crawl_urls=body.crawl_urls,
        upstream_urls=body.upstream_urls,
        limit=body.limit,
    )
    return CorpusCandidatesResponse(
        candidates=[
            CorpusCandidateView(
                domain=candidate.domain,
                signals=[signal.value for signal in candidate.signals],
                evidence=list(candidate.evidence),
                label=candidate.label,
                kind=candidate.kind,
                entry_url=candidate.entry_url,
                verified=candidate.verified,
                score=candidate.score,
            )
            for candidate in candidates
        ],
        nomination=CorpusNominationView(attempted=attempted, count=len(nominated)),
    )
