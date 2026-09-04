import { Brain, RefreshCw } from "lucide-react";

export function MemoryView() {
  return (
    <section aria-label="记忆" className="memory-view">
      <header><Brain aria-hidden="true" size={20} /><h2>记忆</h2></header>
      <p>跨会话摘要与知识蒸馏</p>
      <button type="button"><RefreshCw size={16} aria-hidden="true" />刷新记忆</button>
    </section>
  );
}
