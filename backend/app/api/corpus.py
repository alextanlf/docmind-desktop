from __future__ import annotations

import json
from typing import cast

from fastapi import APIRouter, Request

from app.api.errors import DomainError
from app.corpus.candidates import NominatedSource, derive_candidates, normalize_domain
from app.corpus.crawl import CorpusCrawler
from app.corpus.nomination import DomainNominator
from app.schemas.corpus import (
    CorpusCandidateRequest,
    CorpusCandidatesResponse,
    CorpusCandidateView,
    CorpusNominationView,
    CrawlSourceResponse,
    CrawlSummaryView,
    SaveWebSourcesRequest,
    WebSourcesResponse,
    WebSourceView,
)
from app.storage.models import WebSourceRecord, utc_now
from app.storage.repositories import CorpusStats, WebCorpusStore, WebSourceStore

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


_EMPTY_STATS = CorpusStats(pages=0, chunks=0, last_fetched_at=None)


def _store(request: Request) -> WebSourceStore:
    return cast(WebSourceStore, request.app.state.web_sources)


def _corpus(request: Request) -> WebCorpusStore:
    return cast(WebCorpusStore, request.app.state.web_corpus)


def _crawler(request: Request) -> CorpusCrawler:
    return cast(CorpusCrawler, request.app.state.corpus_crawler)


def _sources_response(
    request: Request, rows: list[WebSourceRecord]
) -> WebSourcesResponse:
    """来源清单 + 每个来源的语料规模。

    规模一次分组查询拿到，而不是每行查一次 —— 列表本来就会整体渲染。
    """
    stats = _corpus(request).stats()
    return WebSourcesResponse(
        sources=[
            WebSourceView(
                domain=row.domain,
                entry_url=row.entry_url,
                label=row.label,
                kind=row.kind,
                verified=row.verified,
                evidence=list(json.loads(row.evidence_json or "[]")),
                page_count=stats.get(row.domain, _EMPTY_STATS).pages,
                chunk_count=stats.get(row.domain, _EMPTY_STATS).chunks,
                last_crawled_at=row.last_crawled_at,
                last_error=row.last_error,
                added_at=row.added_at,
            )
            for row in rows
        ]
    )


@router.get("/sources", response_model=WebSourcesResponse)
async def list_web_sources(request: Request) -> WebSourcesResponse:
    """已确认收录的来源 —— 也就是本地网页索引将来的范围。"""
    return _sources_response(request, _store(request).list())


@router.post("/sources", response_model=WebSourcesResponse)
async def save_web_sources(
    request: Request, body: SaveWebSourcesRequest
) -> WebSourcesResponse:
    """收录（或更新）来源。传整个清单回来，界面一次请求就能刷新。"""
    # 同一批加入的来源共用一个时间戳：它们的 added_at 相同，列表就按域名排，
    # 而不是按"谁先构造"这种用户看不见的顺序 —— 否则刚点完"全部收录"，列表看起来
    # 是乱的。
    stamped = utc_now()
    records = [
        WebSourceRecord(
            added_at=stamped,
            updated_at=stamped,
            domain=item.domain,
            entry_url=item.entry_url,
            label=item.label,
            kind=item.kind,
            verified=item.verified,
            # 同一条证据不重复存：反复点"收录"不该让同一句话堆成十行。
            evidence_json=json.dumps(
                list(dict.fromkeys(item.evidence)), ensure_ascii=False
            ),
        )
        for item in body.sources
    ]
    return _sources_response(request, _store(request).upsert(records))


@router.delete("/sources/{domain}", response_model=WebSourcesResponse)
async def remove_web_source(request: Request, domain: str) -> WebSourcesResponse:
    """移除来源。**删一个本来就不存在的域名也返回成功。**

    界面上的双击、以及两个窗口同时操作，都会产生一次"删一个已经不在的东西"；为此
    弹一个错误只会让用户以为操作失败了。响应始终是删除后的真实清单，所以调用方
    不需要区分这两种情况。
    """
    store = _store(request)
    normalized = normalize_domain(domain)
    if normalized is not None:
        store.remove(normalized)
    return _sources_response(request, store.list())


@router.post("/sources/{domain}/crawl", response_model=CrawlSourceResponse)
async def crawl_web_source(request: Request, domain: str) -> CrawlSourceResponse:
    """抓取一个来源，并把结果记回它自己身上。

    预算写死在默认值里（页数 / 深度）：这是按钮触发的动作，必须能在有限时间内结束。
    抓完整站是另一件事 —— 那需要一个后台任务与进度上报，而不是一个会转很久的请求。

    **同步执行**是有意的：抓取结果（抓到了几页）就是用户按下按钮想立刻看到的东西，
    做成异步任务反而要再补一套进度轮询。代价是请求会占用几秒到几十秒，而这一点在
    界面上是可见的（按钮进入 pending 状态）。
    """
    store = _store(request)
    normalized = normalize_domain(domain)
    source = (
        next((row for row in store.list() if row.domain == normalized), None)
        if normalized is not None
        else None
    )
    if source is None:
        raise DomainError("CORPUS_SOURCE_UNKNOWN", "这个来源不在已收录清单里", 404, False)

    summary = await _crawler(request).crawl(
        domain=source.domain, entry_url=source.entry_url
    )
    # 全失败时把最主要的原因记下来（原因码，文案在渲染层），成功则清掉旧错误。
    store.mark_crawled(source.domain, summary.dominant_failure())
    return CrawlSourceResponse(
        sources=_sources_response(request, store.list()).sources,
        summary=CrawlSummaryView(
            domain=summary.domain,
            pages=summary.pages,
            chunks=summary.chunks,
            kept_pages=summary.kept_pages,
            updated_pages=summary.updated_pages,
            removed_pages=summary.removed_pages,
            skipped_count=len(summary.skipped),
            truncated=summary.truncated,
        ),
    )
