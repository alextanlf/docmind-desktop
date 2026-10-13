import { Brain, RefreshCw } from "lucide-react";
import { useState } from "react";
import { RepositoryScope } from "../repositories/RepositoryScope";
import { useRepositoriesQuery } from "../repositories/repository.queries";
import { DistillationEditor } from "./DistillationEditor";
import { MemoryList } from "./MemoryList";
import { SummaryDetail } from "./SummaryDetail";
import { useMemoryListQuery } from "./memory.queries";
import { useUiStore } from "../../stores/ui-store";

export function MemoryView() {
  const repositories = useRepositoriesQuery();
  const [repositoryIds, setRepositoryIds] = useState<string[]>([]);
  const [kind, setKind] = useState<"session_summary" | "distillation" | undefined>();
  const initialDistillationId = useUiStore((state) => state.selectedDistillationId);
  const [selected, setSelected] = useState<{
    id: string;
    kind: "session_summary" | "distillation";
    sessionId: string | null;
  } | null>(() =>
    initialDistillationId
      ? { id: initialDistillationId, kind: "distillation", sessionId: null }
      : null,
  );
  const query = useMemoryListQuery(repositoryIds.length ? { repositoryIds, kind } : null);
  return (
    <section aria-label="记忆" className="memory-view">
      <header className="view-header">
        <div>
          <Brain aria-hidden="true" size={20} />
          <h2>记忆</h2>
        </div>
        <button
          aria-label="刷新记忆"
          className="icon-button"
          // 列表查询在没选知识库时是关着的（`enabled: false`），此时 refetch 是空操作：
          // 按钮得自己说出来，而不是点下去什么都不发生。
          disabled={!repositoryIds.length}
          onClick={() => void query.refetch()}
          type="button"
        >
          <RefreshCw aria-hidden="true" size={16} />
        </button>
      </header>
      <div className="memory-controls">
        <div className="memory-control">
          <span>知识库</span>
          {/* 原来这里是个 `multiple` 的 `<select>`：Chromium 把它画成原生列表框，
              全局的 `select { height: 36px }` 又只留得下一行 —— 看上去就是个写着
              知识库名的输入框，既没有下拉箭头，也看不出能多选。会话工具栏早就有
              同一个概念（选择知识库）的复选浮层，这里直接用同一个。 */}
          <RepositoryScope
            ariaLabel="记忆知识库"
            emptyText="暂无知识库"
            indexedOnly={false}
            onChange={setRepositoryIds}
            repositories={repositories.data ?? []}
            selectedRepositoryIds={repositoryIds}
          />
        </div>
        <div className="memory-control">
          <span>类型</span>
          <select
            aria-label="记忆类型"
            onChange={(event) => setKind((event.target.value || undefined) as typeof kind)}
            value={kind ?? ""}
          >
            <option value="">全部</option>
            <option value="session_summary">会话摘要</option>
            <option value="distillation">知识蒸馏</option>
          </select>
        </div>
      </div>
      <div className="memory-layout">
        <aside aria-label="记忆列表">
          {repositoryIds.length ? (
            <MemoryList
              onSelect={(id, nextKind, sessionId) => setSelected({ id, kind: nextKind, sessionId })}
              page={query.data}
              selectedId={selected?.id}
            />
          ) : (
            <p className="memory-empty">请选择知识库</p>
          )}
        </aside>
        <main>
          {selected?.kind === "distillation" ? (
            <DistillationEditor distillationId={selected.id} />
          ) : selected?.sessionId ? (
            <SummaryDetail sessionId={selected.sessionId} />
          ) : (
            <p className="memory-empty">选择一条记忆查看详情</p>
          )}
        </main>
      </div>
    </section>
  );
}
