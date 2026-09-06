import type { MemoryItemPage } from "../../../../shared/contracts";

export function MemoryList({
  page,
  selectedId,
  onSelect,
}: {
  page?: MemoryItemPage;
  selectedId?: string | null;
  onSelect(id: string, kind: "session_summary" | "distillation", sessionId: string | null): void;
}) {
  if (!page?.items.length) return <p className="memory-empty">暂无可用记忆</p>;
  return (
    <ul className="memory-list">
      {page.items.map((item) => (
        <li key={item.id}>
          <button
            aria-current={selectedId === item.id ? "page" : undefined}
            onClick={() => onSelect(item.id, item.kind, item.sessionId)}
            type="button"
          >
            <strong>{item.title}</strong>
            <span>{item.kind === "distillation" ? "知识蒸馏" : "会话摘要"}</span>
            <p>{item.excerpt}</p>
          </button>
        </li>
      ))}
    </ul>
  );
}
