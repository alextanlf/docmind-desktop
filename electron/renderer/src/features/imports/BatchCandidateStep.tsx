import { useMemo } from "react";
import type { BatchItem } from "../../../../shared/contracts";
import { useBatchItemsQuery, useBatchQuery } from "./batch-import.queries";
import { useImportStore } from "./import-store";

export function BatchCandidateStep({ batchId, onConfirm, onCancel }: { batchId: string; onConfirm: () => void; onCancel: () => void }) {
  const batch = useBatchQuery(batchId);
  const items = useBatchItemsQuery(batchId);
  const setDecision = useImportStore((s) => s.setBatchDecision);
  const decisions = useImportStore((s) => s.batchDecisions[batchId] ?? {});
  const allItems = useMemo(() => items.data?.pages.flatMap((p) => p.items) ?? [], [items.data]);
  function toggle(item: BatchItem) {
    setDecision(batchId, item.id, decisions[item.id] === "skip" ? (item.decision ?? "create") : "skip");
  }
  return <div className="import-step batch-candidate-step">
    <p>发现 {batch.data?.totalCount ?? allItems.length} 项，已选择 {batch.data?.selectedCount ?? allItems.filter((i) => i.selected).length} 项</p>
    <div className="batch-candidate-table-wrap"><table className="batch-candidate-table"><thead><tr><th>选择</th><th>标题</th><th>决策</th><th>状态</th><th className="batch-secondary-column">路径</th><th className="batch-secondary-column">大小</th></tr></thead><tbody>{allItems.map((item) => { const selected = (decisions[item.id] ?? item.decision) !== "skip"; return <tr key={item.id}><td><input type="checkbox" aria-label={`选择 ${item.displayPath}`} checked={selected} onChange={() => toggle(item)} /></td><td>{item.title}</td><td>{decisions[item.id] ?? item.decision ?? "创建"}</td><td>{item.state}</td><td className="batch-secondary-column">{item.displayPath}</td><td className="batch-secondary-column">{item.sizeBytes}</td></tr>; })}</tbody></table></div>
    {items.hasNextPage ? <button className="button button-secondary" onClick={() => void items.fetchNextPage()} type="button">加载更多</button> : null}
    <div className="dialog-actions"><button className="button button-secondary" onClick={onCancel} type="button">取消</button><button className="button button-primary" onClick={onConfirm} disabled={batch.data?.state !== undefined && batch.data.state !== "awaiting_confirmation"} type="button">确认导入</button></div>
  </div>;
}
