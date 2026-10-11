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

**三、向量通道只做"救援"：词法有命中就用词法，词法空了才用向量。**

这一条是**实测逼出来的**。本地语料常常是英文文档，而问的人用中文：中文切单字、英文
切词，纯中文问句对纯英文正文的 token 交集**必然为空** —— 不是"相关度低"，是结构上
不可能命中。实测（`scripts/ab_corpus_language_gap.py`，10 例严格零重叠用例）：
词法单通道 0/10，向量 10/10。

但**不能**把两条通道无门槛地融合（我第一版就是那么写的，被测试当场抓出来）：向量没有
门槛时会回答**每一个**问题，连无关的也返回几条，而链上"第一个有结果的来源胜出" ——
于是本地语料会挡住上游，有可用上游的用户反而拿到更差的答案。

而加门槛也不行，实测不支持（同一份实验量出的余弦分布）：

| | 余弦 |
|---|---|
| 相关（问句 vs 它的目标页） | 最低 0.469，p10 0.551 |
| 不相关（问句 vs 其它页） | 中位 0.414，p90 0.480，**最高 0.554** |

两个分布几乎完全重叠 —— 任何绝对门槛要么砍掉真正的命中，要么放进无关的页。这恰好
印证了项目那条定论（相似度分数不能判断覆盖度），所以这里**不设门槛**，改用一条不需要
门槛的规则：词法通道有命中时完全按原样走（行为不变，精确查询如标识符不会被语义噪声
顶掉），词法为空时才让向量补位 —— 而那正是今天返回零、只能交给上游的情形。

残余风险（如实记下）：词法为空、而语料其实答不了的问题，现在会得到语料"最好的猜测"
而不是转给上游。缓解手段是模型仍会判断这些片段够不够（证据充分性由生成模型判断），
以及用户点「联网搜索」时进入 explicit 模式 —— **显式要联网时不该被本地语料挡住**，
这条链序规则还没做。

嵌入不可用时（模型未就绪、维度不匹配、索引损坏）自动退回纯词法：`CorpusVectorIndex`
把这类失败都吞成"没有向量"，所以这一级不会因为增强通道坏掉而整个失效。
"""

from __future__ import annotations

import asyncio
import logging

from rank_bm25 import BM25Plus

from app.core.embedding import EmbeddingProvider
from app.core.retrieval import tokenize
from app.corpus.vectors import CorpusVectorIndex
from app.schemas.web_search import NormalizedSearchResult, SearchRequest, SearchResponse
from app.search.provider import SearchProviderError
from app.storage.repositories import WebCorpusStore

logger = logging.getLogger(__name__)

#: 向量通道取多少个候选交给融合。
#:
#: 取 20 而不是"等于要的结果数"：融合是**按排名**合并的，候选太少会让一条通道的
#: 第 4 名直接消失；而多取 20 条几乎不花钱（ANN 查询 + 一次按 id 取正文）。
_SEMANTIC_TOP_K = 20

#: 送进 LLM 的单条正文上限，与其它搜索源一致（见 schemas/web_search.py）。
_CONTENT_LIMIT = 50 * 1024
_SNIPPET_LIMIT = 10_000


class WebCorpusSearchProvider:
    """在本地网页语料上做 BM25 检索。"""

    name = "web_corpus"

    def __init__(
        self,
        store: WebCorpusStore,
        *,
        vectors: CorpusVectorIndex | None = None,
        embedder: EmbeddingProvider | None = None,
    ) -> None:
        self.store = store
        self.vectors = vectors
        self.embedder = embedder

    def available(self) -> bool:
        """有语料才可用。

        这一条不是形式：链上如果放着一个永远返回空结果的来源，每次搜索都要白跑一次
        全库扫描，而用户看到的是"搜索变慢了但没多出任何来源"。
        """
        return bool(self.store.stats())

    async def search(self, request: SearchRequest) -> SearchResponse:
        # BM25 是纯 CPU 的全库扫描，放进线程免得阻塞事件循环（其它来源都在等网络，
        # 而这里在算分数）。
        lexical = await asyncio.to_thread(self._lexical_ranking, request.query)
        # 词法有命中就到此为止：行为与加向量之前完全一致。
        if not lexical:
            lexical = await self._semantic_ranking(request.query)
        results = await asyncio.to_thread(self._collect, request, ranked_ids=lexical)
        if not results:
            raise SearchProviderError("SEARCH_PROVIDER_ERROR", "本地网页索引没有匹配内容")
        return SearchResponse(results=results, provider=self.name)

    async def _semantic_ranking(self, query: str) -> list[str]:
        """向量通道的候选块 id，按相似度降序。不可用/失败时返回空列表。

        这里**不做相似度门槛**，理由与词法通道一致：项目已经定论相似度分数不能判断
        覆盖度（见 chat/service.py 里关于预检索闸门那段）。取 top-k 交给 RRF 排序，
        而不是拿一个绝对值当作"够不够相关"的判决。
        """
        if self.vectors is None or self.embedder is None:
            return []
        try:
            embedding = await self.embedder.embed_query(query)
        except Exception as error:  # noqa: BLE001 - 增强通道，失败就退回词法
            logger.warning("corpus query embedding failed: %s", error)
            return []
        return [hit.id for hit in self.vectors.search(embedding, _SEMANTIC_TOP_K)]

    def _lexical_ranking(self, query: str) -> list[str]:
        """词法通道的候选块 id，按 BM25Plus 降序。没有任何词面重合时空列表。"""
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        chunks = self.store.chunks()
        if not chunks:
            return []

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
        seen: set[str] = set()
        ranked_ids: list[str] = []
        for (chunk, _), _score in ranked:
            if chunk.id in seen:
                continue
            seen.add(chunk.id)
            ranked_ids.append(chunk.id)
        return ranked_ids

    def _collect(
        self, request: SearchRequest, *, ranked_ids: list[str]
    ) -> list[NormalizedSearchResult]:
        """把候选块 id 变成按页去重的结果。

        id 可能已经不存在（悬空向量：索引里有、语料里删了），这里自然过滤掉。
        """
        if not ranked_ids:
            return []
        # 多取一些再去重：同一页可能有多个块命中，取少了会因为去重后不够 N 条。
        candidates = self.store.chunks_by_ids(ranked_ids[: request.max_results * 3])
        results: list[NormalizedSearchResult] = []
        seen_urls: set[str] = set()
        for chunk_id in ranked_ids:
            chunk = candidates.get(chunk_id)
            if chunk is None or chunk.url in seen_urls:
                # 悬空向量（索引里有、语料里已删）在这里被自然过滤掉。
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
