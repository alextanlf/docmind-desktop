from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from app.schemas.chat import Citation, DocumentCitation, MemoryCitation, WebCitation


@dataclass(frozen=True)
class ContextBundle:
    sources: tuple[Citation, ...]
    registry: dict[str, Citation]


class ContextAssembler:
    def assemble(self, document_hits: list, memory_hits: list) -> ContextBundle:
        sources: list[Citation] = []
        for index, hit in enumerate(document_hits[:5], 1):
            sources.append(DocumentCitation(source_id=f"S{index}", chunk_id=hit.chunk_id, document_id=hit.document_id, title=hit.document_title, section_path=hit.section_path, page_number=hit.page_number, excerpt=hit.text, source_url=hit.source_url))
        seen: set[tuple[str, str]] = set()
        for hit in memory_hits:
            key = (hit.kind, hit.source_id)
            if key in seen: continue
            seen.add(key)
            index = len([source for source in sources if getattr(source, "kind", None) == "memory"]) + 1
            sources.append(MemoryCitation(source_id=f"M{index}", memory_kind=hit.kind, memory_id=hit.source_id, title="会话摘要" if hit.kind == "session_summary" else "知识蒸馏", excerpt=hit.text, session_id=hit.metadata.get("session_id")))
        return ContextBundle(tuple(sources), MappingProxyType({source.source_id: source for source in sources}))

    def register_web(self, run_id, results: list, *, start_index: int = 1) -> ContextBundle:
        # `start_index` 存在的原因：agent 循环里一个回答可能触发多轮搜索，每轮
        # `register_web` 都从 W1 起编号会让第二轮的 W1 覆盖第一轮 —— 模型引用
        # [W1] 时指向哪一条完全取决于合并顺序。调用方传累计偏移量即可避免。
        sources = tuple(
            WebCitation(
                source_id=f"W{start_index + offset}",
                search_run_id=str(run_id),
                result_id=result.id,
                title=result.title,
                excerpt=result.snippet or result.content[:5000],
                source_url=result.canonical_url,
                retrieved_at=result.created_at,
            )
            for offset, result in enumerate(results[:10])
        )
        return ContextBundle(
            sources, MappingProxyType({source.source_id: source for source in sources})
        )
