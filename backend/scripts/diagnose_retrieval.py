"""检索诊断脚本：回答「为什么这个知识库问不出来」。

复现用户可见的失败链路，并逐层打印数值，避免再靠猜：

1. 语种判定与门槛（门槛按【提问语言】而非语料语言选取）
2. 向量通道 top-k 相似度
3. BM25 词法通道命中数与最高分
4. 检索门控结果（retrieval.py 的 max_score < threshold 短路）
5. 证据充分性门控（evidence.py 的硬编码 0.65）
6. 关闭门控后的 RRF 融合排序（看排序本身是否正确）

--calibrate 模式额外做两件事，用于判断「该不该换嵌入模型」：

7. 索引新鲜度：向量库里存的向量 vs 用当前模型重新编码同一文本，余弦若明显 < 1.0
   说明索引由另一条推理路径（如 fp32）建立，与当前查询编码器不同源，必须重建。
8. 分数分布：对全库所有 chunk 算相似度，给出 top1 / top2 / 中位数与「区分度」，
   用来判断固定绝对门槛是否落在正确命中的天花板上。

用法（必须从 backend/ 运行，alembic.ini 是相对路径）::

    env -u PYTHONPATH .venv/bin/python scripts/diagnose_retrieval.py \
        --data-dir ~/Library/Application\\ Support/docmind-desktop \
        --repo 123 \
        --query "什么是Radar-APLANC"

加 --ranking 可额外打印关闭门控后的完整融合排序。
加 --calibrate 可额外做索引新鲜度检查与全库分数分布。
加 --repo-list 可列出全部知识库名称。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", required=True, type=Path, help="DocMind 数据目录（Electron 态为 userData）")
    parser.add_argument("--repo", help="知识库名称或 id")
    parser.add_argument("--query", action="append", default=[], help="待诊断的问题，可重复传入")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--ranking", action="store_true", help="额外打印关闭门控后的融合排序")
    parser.add_argument("--calibrate", action="store_true", help="额外做索引新鲜度检查与全库分数分布")
    parser.add_argument("--repo-list", action="store_true", help="仅列出知识库后退出")
    return parser.parse_args()


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


async def calibrate(repository_id: str, db, store, provider, queries: list[str]) -> None:
    """索引新鲜度 + 全库分数分布，回答「问题出在模型还是用法」。"""
    from sqlalchemy import select

    from app.storage.models import DocumentChunkRecord

    with db.session() as session:
        chunks = list(
            session.scalars(
                select(DocumentChunkRecord)
                .where(DocumentChunkRecord.repository_id == repository_id)
                .order_by(DocumentChunkRecord.chunk_index)
            )
        )
    indexed = [chunk for chunk in chunks if chunk.vector_id]
    if not indexed:
        print("\n[calibrate] 该知识库没有已索引的分块，跳过。")
        return

    collection = store._collection(repository_id)
    payload = collection.get(ids=[chunk.vector_id for chunk in indexed], include=["embeddings"])
    stored = {
        identifier: list(vector)
        for identifier, vector in zip(payload["ids"], payload["embeddings"], strict=True)
    }
    fresh = await provider.embed_documents([chunk.text for chunk in indexed])

    print("\n" + "=" * 78)
    print("[calibrate 1/2] 索引新鲜度（存的向量 vs 当前模型重编码）")
    freshness = [
        _cosine(stored[chunk.vector_id], vector)
        for chunk, vector in zip(indexed, fresh, strict=True)
        if chunk.vector_id in stored
    ]
    if freshness:
        print(f"  cos 均值={sum(freshness) / len(freshness):.4f}  最小={min(freshness):.4f}  "
              f"（{len(freshness)} 个分块）")
        if min(freshness) >= 0.999:
            print("  → 索引与当前查询编码器同源。")
        else:
            print("  → 索引与当前查询编码器【不同源】：相似度被系统性压低，也会打乱接近候选的排序。")
            print("     建议重建该知识库的嵌入索引后再评估门槛。")

    chunks_text = {chunk.id: chunk.text for chunk in indexed}
    for query in queries:
        embedding = await provider.embed_query(query)
        scores = sorted(
            ((_cosine(embedding, stored[chunk.vector_id]), chunk.id) for chunk in indexed
             if chunk.vector_id in stored),
            key=lambda item: -item[0],
        )
        if not scores:
            continue
        values = [score for score, _ in scores]
        top1 = values[0]
        median = values[len(values) // 2]
        print(f"\n[calibrate 2/2] 全库分数分布 · 查询 {query!r}（{len(values)} 个分块）")
        print(f"  top1={top1:.4f}  top2={values[1] if len(values) > 1 else float('nan'):.4f}  "
              f"中位数={median:.4f}  最低={values[-1]:.4f}")
        print(f"  区分度 top1-中位数 = {top1 - median:+.4f}")
        print(f"  top1 分块: {chunks_text.get(scores[0][1], '')[:90]!r}")
        print("  → 固定绝对门槛要求 top1 越过阈值；若正确命中的 top1 天生低于阈值，"
              "则该门槛对此类查询必然错杀，与模型强弱无关。")


async def run(args: argparse.Namespace) -> int:
    data_dir = args.data_dir.expanduser()
    os.environ.setdefault("DOCMIND_SESSION_TOKEN", "diagnostic")
    os.environ["DOCMIND_DATA_DIR"] = str(data_dir)

    from sqlalchemy import select

    from app.chat.evidence import decide_evidence
    from app.config import get_settings
    from app.core.embedding import create_embedding_provider
    from app.core.multilingual import detect_language, retrieval_threshold
    from app.core.retrieval import BM25Index, HybridRetriever, tokenize
    from app.storage.database import Database
    from app.storage.models import RepositoryRecord
    from app.storage.vectorstore import PersistentVectorStore

    settings = get_settings()
    database_url = f"sqlite:///{settings.data_dir / 'database' / 'docmind.sqlite3'}"
    db = Database(database_url)

    with db.session() as session:
        repositories = list(session.scalars(select(RepositoryRecord)))

    if args.repo_list or not args.repo:
        print("知识库列表：")
        for repository in repositories:
            marker = " <== 命中" if args.repo and (repository.id == args.repo or repository.name == args.repo) else ""
            print(f"  {repository.id}  {repository.name!r}{marker}")
        if not args.repo:
            return 0

    matched = [r for r in repositories if r.id == args.repo or r.name == args.repo]
    if not matched:
        print(f"未找到知识库 {args.repo!r}")
        return 2
    repository = matched[0]

    store = PersistentVectorStore(settings.vectorstore_settings)
    provider = create_embedding_provider(settings.embedding_settings)
    status = await provider.ensure_ready()
    print(f"嵌入模型: {status.state} / {status.message}")
    print(f"配置阈值 rag_similarity_threshold = {settings.rag_similarity_threshold}"
          "（注意：该值在 HybridRetriever.search 中并未被使用）")
    print(f"知识库: {repository.name!r} ({repository.id})")
    if not args.query:
        return 0

    bm25 = BM25Index(db)
    retriever = HybridRetriever(
        database=db,
        vector_store=store,
        embedding_provider=provider,
        similarity_threshold=settings.rag_similarity_threshold,
    )

    for query in args.query:
        language = detect_language(query)
        threshold = retrieval_threshold(language)
        embedding = await provider.embed_query(query)
        raw = store.query(repository.id, embedding, top_k=10)
        keyword = bm25.search(query, [repository.id], top_k=10)

        print("\n" + "=" * 78)
        print(f"查询: {query!r}")
        print(f"  长度={len(query)}  分词={tokenize(query)}")
        print(f"  语种判定={language}  →  检索门槛={threshold}")
        print(f"  BM25 命中={len(keyword)} 条" + (f"  最高分={keyword[0][1]:.3f}" if keyword else ""))
        print(f"  向量 top-3 相似度={[round(hit.similarity, 4) for hit in raw[:3]]}")
        best = raw[0].similarity if raw else 0.0
        blocked = best < threshold
        print(f"  ① 检索门控: {best:.4f} {'<' if blocked else '>='} {threshold} → "
              f"{'被拦下（返回空 hits，直接走「未覆盖」话术）' if blocked else '通过'}")
        print(f"  ② 证据门控(硬编码 0.65): max={best:.4f} → "
              f"{'不充分' if best < 0.65 else '充分'}  (decide_evidence)")

        if args.ranking:
            result = await retriever.search(query, [repository.id], top_k=args.top_k)
            print(f"  门控内融合排序 (max_score={result.max_score:.4f}, {len(result.hits)} 条):")
            for index, hit in enumerate(result.hits, 1):
                vector = f"{hit.vector_score:.4f}" if hit.vector_score is not None else "-"
                keyword_score = f"{hit.keyword_score:.3f}" if hit.keyword_score is not None else "-"
                print(f"    {index}. fused={hit.fused_score:.5f} vec={vector} bm25={keyword_score} "
                      f":: {hit.text[:80]!r}")
            if not result.hits:
                print("    （空 —— 门控拦下了全部候选，可临时把 multilingual.retrieval_threshold 置 0 再看排序）")

    if args.calibrate:
        await calibrate(repository.id, db, store, provider, args.query)

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
