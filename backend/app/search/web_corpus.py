"""把本地网页语料接成搜索链上的一级。

为什么值得单独做一级：这是**唯一不花额度、不出网、且引用可复现**的来源。厂商内置
联网要先有 key、Tavily 免密钥档会打满、而这两者都要求用户在那个时刻能联网。本地语料
一旦抓下来就在硬盘上，断网也能回答，而且引用的是我们真正读过的正文。

## 两个刻意的设计选择

**一、不做相似度门槛。** 排序用 BM25，只过滤"零分"（一个查询词都没命中）的结果。
项目已经定论：相似度分数不能判断覆盖度（见 `chat/service.py` 里关于预检索闸门那段），
所以这里不引入一个把"够不够相关"交给阈值的闸门 —— 那正是被否定的信号。

**二、排序用 BM25Plus 而不是 Okapi。** 本地语料常常只有几页，而 Okapi 的 IDF 在
N 小时会退化成 0 或负数（同一个词出现在一半文档里时恰好为 0），"命中"与"没命中"就
分不开了。BM25Plus 的 IDF 是 ``log((N+1)/n)``，小语料上仍为正。

**三、没有向量召回。** 嵌入模型可能还没下载（首次使用会触发一次大下载），把它放进
抓取与检索的关键路径，会让"抓一个站"变成"先下载 500MB 模型"。BM25 已经能让抓下来的
正文参与检索；向量召回留到语料规模真的需要它的时候再加。
"""

from __future__ import annotations

import asyncio

from rank_bm25 import BM25Plus

from app.core.retrieval import tokenize
from app.schemas.web_search import NormalizedSearchResult, SearchRequest, SearchResponse
from app.search.provider import SearchProviderError
from app.storage.repositories import WebCorpusStore

#: 送进 LLM 的单条正文上限，与其它搜索源一致（见 schemas/web_search.py）。
_CONTENT_LIMIT = 50 * 1024
_SNIPPET_LIMIT = 10_000


class WebCorpusSearchProvider:
    """在本地网页语料上做 BM25 检索。"""

    name = "web_corpus"

    def __init__(self, store: WebCorpusStore) -> None:
        self.store = store

    def available(self) -> bool:
        """有语料才可用。

        这一条不是形式：链上如果放着一个永远返回空结果的来源，每次搜索都要白跑一次
        全库扫描，而用户看到的是"搜索变慢了但没多出任何来源"。
        """
        return bool(self.store.stats())

    async def search(self, request: SearchRequest) -> SearchResponse:
        # BM25 是纯 CPU 的全库扫描，放进线程免得阻塞事件循环（其它来源都在等网络，
        # 而这里在算分数）。
        results = await asyncio.to_thread(self._search_sync, request)
        if not results:
            raise SearchProviderError("SEARCH_PROVIDER_ERROR", "本地网页索引没有匹配内容")
        return SearchResponse(results=results, provider=self.name)

    def _search_sync(self, request: SearchRequest) -> list[NormalizedSearchResult]:
        query_tokens = tokenize(request.query)
        if not query_tokens:
            return []
        chunks = self.store.chunks()
        if not chunks:
            return []

        # 「有没有关系」先由词面重合直接判断，**不交给 BM25 的 IDF**。
        #
        # 🔴 原因是实测出来的：语料只有几页时 Okapi 的 IDF 会退化 —— 一个词恰好出现在
        # 一半文档里时 `log((N-n+0.5)/(n+0.5)) = 0`，出现在更多文档里时为负，于是"命中"
        # 与"没命中"分数相同或更低，按分数过滤会把真正命中的页也丢掉。而"某个来源只抓
        # 到三五页"在本地语料里是常态，不是边缘情况。
        #
        # 因此：先用重合筛出候选，再让 BM25Plus 在**候选之间**排序。它的 IDF 是
        # `log((N+1)/n)`，在 N 小时仍为正，代价是对高频词的惩罚不如 Okapi 强 —— 而
        # 我们要的只是把最相关的那页排到前面，不是拿分数当相关性阈值（项目已经定论：
        # 相似度分数不能判断覆盖度）。
        query_set = set(query_tokens)
        candidates: list[tuple[object, list[str]]] = []
        for chunk in chunks:
            tokens = tokenize(chunk.text)
            if tokens and query_set & set(tokens):
                candidates.append((chunk, tokens))
        if not candidates:
            return []

        scores = BM25Plus([tokens for _, tokens in candidates]).get_scores(query_tokens)
        ranked = sorted(
            zip(candidates, scores, strict=True),
            key=lambda item: (-float(item[1]), item[0][0].id),
        )

        results: list[NormalizedSearchResult] = []
        seen_urls: set[str] = set()
        for (chunk, _), _score in ranked:
            if chunk.url in seen_urls:
                # 同一页的多个块只出一条：搜索结果的单位是页面，不是块。
                continue
            seen_urls.add(chunk.url)
            results.append(
                NormalizedSearchResult(
                    rank=len(results) + 1,
                    canonical_url=chunk.url,
                    title=(chunk.title or chunk.url)[:512],
                    snippet=chunk.text[:_SNIPPET_LIMIT],
                    content=chunk.text[:_CONTENT_LIMIT],
                )
            )
            if len(results) >= request.max_results:
                break
        return results
