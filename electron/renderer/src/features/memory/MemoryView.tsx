import { Brain, RefreshCw } from "lucide-react";
import { useState } from "react";
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
  const [selected, setSelected] = useState<{ id: string; kind: "session_summary" | "distillation"; sessionId: string | null } | null>(() => initialDistillationId ? { id: initialDistillationId, kind: "distillation", sessionId: null } : null);
  const query = useMemoryListQuery(repositoryIds.length ? { repositoryIds, kind } : null);
  return <section aria-label="记忆" className="memory-view">
    <header className="view-header"><div><Brain aria-hidden="true" size={20} /><h2>记忆</h2></div><button aria-label="刷新记忆" className="icon-button" onClick={() => void query.refetch()} type="button"><RefreshCw aria-hidden="true" size={16} /></button></header>
    <div className="memory-controls"><label>知识库<select aria-label="记忆知识库" multiple onChange={(event) => setRepositoryIds(Array.from(event.currentTarget.selectedOptions, (option) => option.value))} value={repositoryIds}>{repositories.data?.map((repository) => <option key={repository.id} value={repository.id}>{repository.name}</option>)}</select></label><label>类型<select aria-label="记忆类型" onChange={(event) => setKind((event.target.value || undefined) as typeof kind)} value={kind ?? ""}><option value="">全部</option><option value="session_summary">会话摘要</option><option value="distillation">知识蒸馏</option></select></label></div>
    <div className="memory-layout"><aside aria-label="记忆列表">{repositoryIds.length ? <MemoryList onSelect={(id, nextKind, sessionId) => setSelected({ id, kind: nextKind, sessionId })} page={query.data} selectedId={selected?.id} /> : <p className="memory-empty">请选择知识库</p>}</aside><main>{selected?.kind === "distillation" ? <DistillationEditor distillationId={selected.id} /> : selected?.sessionId ? <SummaryDetail sessionId={selected.sessionId} /> : <p className="memory-empty">选择一条记忆查看详情</p>}</main></div>
  </section>;
}
