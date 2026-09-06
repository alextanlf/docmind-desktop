import { RefreshCw, Trash2 } from "lucide-react";
import { useSummaryMutations, useSummaryQuery } from "./memory.queries";

export function SummaryDetail({ sessionId }: { sessionId: string }) {
  const query = useSummaryQuery(sessionId);
  const actions = useSummaryMutations(sessionId);
  if (!query.data) return <p className="memory-empty">摘要不存在</p>;
  return (
    <section aria-label="会话摘要">
      <header className="memory-editor-toolbar">
        <h3>会话摘要</h3>
        <div className="session-actions">
          <button
            aria-label="重新生成摘要"
            className="icon-button"
            onClick={() => actions.regenerate.mutate()}
            type="button"
          >
            <RefreshCw aria-hidden="true" size={16} />
          </button>
          <button
            aria-label="删除摘要"
            className="icon-button"
            onClick={() => actions.remove.mutate()}
            type="button"
          >
            <Trash2 aria-hidden="true" size={16} />
          </button>
        </div>
      </header>
      <span role="status">
        {query.data.state === "failed" ? "生成失败，可重试" : query.data.state}
      </span>
      <div className="memory-summary-content">{query.data.content}</div>
    </section>
  );
}
