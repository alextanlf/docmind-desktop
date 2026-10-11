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

**三、两条通道怎么合：词法有命中就**融合**，词法为空才由向量单独作答。**

这一条是**实测逼出来的**。本地语料常常是英文文档，而问的人用中文：中文切单字、英文
切词，纯中文问句对纯英文正文的 token 交集**必然为空** —— 不是"相关度低"，是结构上
不可能命中。实测（`scripts/ab_corpus_language_gap.py`，10 例严格零重叠用例）：
词法单通道 0/10，向量 10/10。

但**不能**把"词法为空时用向量"这条救援规则换成"无条件融合"（我第一版就是那么写的，
被测试当场抓出来）：向量没有门槛时会回答**每一个**问题，连无关的也返回几条，而链上
"第一个有结果的来源胜出" —— 于是本地语料会挡住上游。而加门槛也不行，实测不支持：

| 判别器 | 语料内有答案 | 同领域但语料里没答案 | 可分？ |
|---|---|---|---|
| top1 余弦 | 0.473–0.716 | 0.430–0.528 | ✗ |
| top1 − top2 | 0.013–0.287 | 0.003–0.070 | ✗ |
| top1 − 中位数 | 0.123–0.346 | 0.057–0.143 | ✗ |
| top1 的 z 值 | 2.133–3.639 | 1.512–2.890 | ✗ |

（`scripts/ab_corpus_relevance_gate.py`，22 条查询、三类负例。绝对值门槛能滤掉"领域外"
的问题，但**滤不掉"同领域而语料里没有答案"** —— 而那恰恰是挡住上游的真实来源：
用户问 PostgreSQL 主从复制，语料返回 SQLite 的页面，相似度 0.447，看着很相关，没有答案。）

所以规则按"**能不能作答**"来分层，而不是按分数：

- 词法有命中 → **融合**两条通道的排名（RRF）。这时语料本来就要作答，融合只是让排序更
  准：一个词面弱命中、语义更强的页面可以排到前面（此前它会被直接丢掉）。
- 词法为空 → 向量**单独**作答（救援）。这正是中文问句的情形：词法上零重合，只有向量
  找得回来。

换句话说：融合只改变"已经决定作答"时的**排序**，不改变"要不要作答"的**路由** ——
所以它不会让更多问题被本地语料吃掉。

残余风险（如实记下）：词法为空、而语料其实答不了的问题，现在会得到语料"最好的猜测"
而不是转给上游。缓解手段是模型仍会判断这些片段够不够（证据充分性由生成模型判断）。

用户点「联网搜索」时**不会被本地语料挡住**：那一档走 explicit 模式，链会把本来源挪到
最后（`FallbackSearchProvider.ordered`，靠 `local = True` 这个标记）。所以这里是本地
优先、显式联网时降级为兜底 —— 而不是无条件优先。

嵌入不可用时（模型未就绪、维度不匹配、索引损坏）自动退回纯词法：`CorpusVectorIndex`
把这类失败都吞成"没有向量"，所以这一级不会因为增强通道坏掉而整个失效。
"""

from __future__ import annotations

import asyncio
import logging

from rank_bm25 import BM25Plus

from app.core.embedding import EmbeddingProvider
from app.core.retrieval import reciprocal_rank_fusion, tokenize
from app.corpus.vectors import CorpusVectorIndex
from app.schemas.web_search import NormalizedSearchResult, SearchRequest, SearchResponse
from app.search.provider import SearchProviderError
from app.storage.repositories import WebCorpusStore

logger = logging.getLogger(__name__)


def _combine(lexical: list[str], semantic: list[str]) -> list[str]:
    """两条通道的候选合成一个排名。

    - 词法有命中：RRF 融合（语义通道可以把一个词面弱命中但更贴题的页面顶上来）。
    - 词法为空：只用语义通道 —— 这正是零词面重合的中文问句能被回答的原因。
    - 两条都空：空列表，调用方会报"没有匹配内容"，让链把问题交给上游。
    """
    if lexical and semantic:
        return [item.chunk_id for item in reciprocal_rank_fusion(semantic, lexical, k=60)]
    return lexical or semantic

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

    #: 不依赖网络。链据此在**显式联网**时把它挪到最后 —— 用户点「联网搜索」时应该先
    #: 拿到网上的新信息，本地语料只作为上游全不可用时的兜底。
    local = True

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
        semantic = await self._semantic_ranking(request.query)
        ranked_ids = _combine(lexical, semantic)
        results = await asyncio.to_thread(self._collect, request, ranked_ids=ranked_ids)
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
