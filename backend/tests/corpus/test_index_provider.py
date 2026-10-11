"""本地语料作为搜索链的一级：命中就给结果，没命中就让位给上游。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.schemas.web_search import NormalizedSearchResult, SearchRequest, SearchResponse
from app.search.fallback import FallbackSearchProvider
from app.search.provider import SearchProviderError
from app.search.web_corpus import WebCorpusSearchProvider
from app.storage.database import Database
from app.storage.models import WebCorpusChunkRecord, WebCorpusPageRecord, new_id
from app.storage.repositories import WebCorpusStore


def store_for(tmp_path: Path) -> WebCorpusStore:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'index.sqlite3'}")
    database.upgrade()
    return WebCorpusStore(database)


def seed(store: WebCorpusStore, domain: str, pages: list[tuple[str, str, list[str]]]) -> None:
    """写入语料。一个域名的页与块必须一次写完 —— 存储层是按域名整体替换的。"""
    page_rows: list[WebCorpusPageRecord] = []
    chunk_rows: list[WebCorpusChunkRecord] = []
    for url, title, texts in pages:
        page_id = new_id()
        page_rows.append(
            WebCorpusPageRecord(
                id=page_id, domain=domain, url=url, title=title, content_hash="x", byte_size=1
            )
        )
        for ordinal, text in enumerate(texts):
            chunk_rows.append(
                WebCorpusChunkRecord(
                    page_id=page_id,
                    domain=domain,
                    url=url,
                    title=title,
                    ordinal=ordinal,
                    text=text,
                )
            )
    store.replace_domain(domain, page_rows, chunk_rows)


class StubProvider:
    name = "stub_upstream"

    def __init__(self) -> None:
        self.calls = 0

    def available(self) -> bool:
        return True

    async def search(self, request: SearchRequest) -> SearchResponse:
        self.calls += 1
        return SearchResponse(
            results=[
                NormalizedSearchResult(
                    rank=1,
                    canonical_url="https://upstream.example/x",
                    title="上游结果",
                    snippet="…",
                    content="…",
                )
            ],
            provider=self.name,
        )


@pytest.mark.asyncio
async def test_a_term_match_in_the_local_corpus_is_returned_with_its_source_url(
    tmp_path: Path,
) -> None:
    store = store_for(tmp_path)
    seed(
        store,
        "docs.test",
        [
            ("https://docs.test/asyncio", "asyncio 事件循环", ["asyncio 事件循环与任务调度"]),
            ("https://docs.test/sqlite", "SQLite FTS5", ["SQLite FTS5 建索引方式"]),
        ],
    )

    response = await WebCorpusSearchProvider(store).search(
        SearchRequest(query="asyncio 事件循环", max_results=5)
    )

    assert response.provider == "web_corpus"
    assert [str(result.canonical_url) for result in response.results] == ["https://docs.test/asyncio"]
    # 引用要能回到原本那一页。
    assert response.results[0].title == "asyncio 事件循环"


@pytest.mark.asyncio
async def test_one_result_per_page_even_when_several_chunks_match(tmp_path: Path) -> None:
    """搜索结果的单位是页面，不是块 —— 同一页出现三次只是噪声。"""
    store = store_for(tmp_path)
    seed(
        store,
        "docs.test",
        [
            (
                "https://docs.test/asyncio",
                "asyncio",
                ["asyncio 事件循环一", "asyncio 事件循环二", "asyncio 事件循环三"],
            )
        ],
    )

    response = await WebCorpusSearchProvider(store).search(
        SearchRequest(query="asyncio 事件循环", max_results=5)
    )

    assert len(response.results) == 1


@pytest.mark.asyncio
async def test_no_term_overlap_yields_nothing_so_the_chain_can_move_on(tmp_path: Path) -> None:
    """零重合不是"相关度低"，是"没关系"。返回空结果才让上游有机会回答。"""
    store = store_for(tmp_path)
    seed(store, "docs.test", [("https://docs.test/a", "A", ["完全无关的内容"])])

    with pytest.raises(SearchProviderError):
        await WebCorpusSearchProvider(store).search(
            SearchRequest(query="kubernetes 调度器", max_results=5)
        )


@pytest.mark.asyncio
async def test_an_empty_corpus_is_not_available(tmp_path: Path) -> None:
    """链上放一个永远返回空的来源，只会让每次搜索白扫一遍全库。"""
    store = store_for(tmp_path)

    provider = WebCorpusSearchProvider(store)

    assert provider.available() is False
    seed(store, "docs.test", [("https://docs.test/a", "A", ["asyncio 事件循环"])])
    assert provider.available() is True


@pytest.mark.asyncio
async def test_the_local_corpus_is_tried_before_the_upstream(tmp_path: Path) -> None:
    """本地语料排在最前：它是唯一不花额度、不出网、引用可复现的来源。"""
    store = store_for(tmp_path)
    seed(store, "docs.test", [("https://docs.test/asyncio", "asyncio", ["asyncio 事件循环"])])
    upstream = StubProvider()
    chain = FallbackSearchProvider([WebCorpusSearchProvider(store), upstream])

    hit = await chain.search(SearchRequest(query="asyncio 事件循环", max_results=5))
    assert hit.provider == "web_corpus"
    assert upstream.calls == 0

    # 本地没命中时，链必须继续往下走 —— 否则"本地优先"会变成"本地独占"。
    miss = await chain.search(SearchRequest(query="kubernetes 调度器", max_results=5))
    assert miss.provider == "stub_upstream"
    assert upstream.calls == 1
