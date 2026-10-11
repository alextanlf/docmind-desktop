import { useEffect, useMemo } from "react";
import type { BatchItem } from "../../../../shared/contracts";
import {
  useBatchDiagnosticsQuery,
  useBatchItemsQuery,
  useBatchQuery,
} from "./batch-import.queries";
import { useImportStore } from "./import-store";
import { groupRejections } from "./rejection-copy";
const EMPTY_DECISIONS: Record<string, "include" | "skip" | "defer"> = {};

/** 每个原因下面最多列几个具体 URL —— 用户要的是"哪一类、有多少"，不是一份完整清单。 */
const URLS_PER_GROUP = 5;

export function BatchCandidateStep({
  batchId,
  onConfirm,
  onCancel,
  pending = false,
}: {
  batchId: string;
  onConfirm: () => void;
  onCancel: () => void;
  pending?: boolean;
}) {
  const batch = useBatchQuery(batchId);
  const items = useBatchItemsQuery(batchId);
  // 「为什么只有 12 项」正是用户在这一步会问的问题，所以诊断信息就放在这里，
  // 而不是等导入结束之后。
  const diagnostics = useBatchDiagnosticsQuery(batchId);
  const rejectionGroups = useMemo(
    () => groupRejections(diagnostics.data?.items ?? []),
    [diagnostics.data],
  );
  const setDecision = useImportStore((s) => s.setBatchDecision);
  const setBatchItems = useImportStore((s) => s.setBatchItems);
  const decisions = useImportStore((s) => s.batchDecisions[batchId] ?? EMPTY_DECISIONS);
  const allItems = useMemo(() => items.data?.pages.flatMap((p) => p.items) ?? [], [items.data]);
  useEffect(() => setBatchItems(batchId, allItems), [batchId, allItems, setBatchItems]);
  function effectiveDecision(item: BatchItem) {
    const local = decisions[item.id];
    if (local) return local;
    if (item.decision && item.allowedActions.includes(item.decision)) return item.decision;
    if (!item.selected) return "skip" as const;
    return item.allowedActions.find((a) => a !== "skip") ?? ("skip" as const);
  }
  return (
    <div className="import-step batch-candidate-step">
      <p>
        发现 {batch.data?.totalCount ?? allItems.length} 项，已选择{" "}
        {batch.data?.selectedCount ?? allItems.filter((i) => i.selected).length} 项
      </p>
      {diagnostics.data && diagnostics.data.total > 0 ? (
        <details className="batch-diagnostics">
          <summary>另有 {diagnostics.data.total} 个页面没有收录（查看原因）</summary>
          <ul className="batch-diagnostics-list">
            {rejectionGroups.map((group) => (
              <li key={group.reason}>
                <p>
                  <strong>
                    {group.label}（{group.urls.length}）
                  </strong>
                  <span>{group.hint}</span>
                </p>
                <ul>
                  {group.urls.slice(0, URLS_PER_GROUP).map((url) => (
                    <li key={url}>{url}</li>
                  ))}
                  {group.urls.length > URLS_PER_GROUP ? (
                    <li>…另有 {group.urls.length - URLS_PER_GROUP} 个</li>
                  ) : null}
                </ul>
              </li>
            ))}
          </ul>
          {diagnostics.data.truncated ? (
            <p>列表只列出前 {diagnostics.data.items.length} 条地址。</p>
          ) : null}
        </details>
      ) : null}
      <div className="batch-candidate-table-wrap">
        <table className="batch-candidate-table">
          <thead>
            <tr>
              <th>选择</th>
              <th>标题</th>
              <th>决策</th>
              <th>状态</th>
              <th className="batch-secondary-column">路径</th>
              <th className="batch-secondary-column">大小</th>
            </tr>
          </thead>
          <tbody>
            {allItems.map((item) => {
              const decision = effectiveDecision(item);
              const selected = decision !== "skip";
              return (
                <tr key={item.id}>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`选择 ${item.displayPath}`}
                      checked={selected}
                      onChange={() =>
                        setDecision(
                          batchId,
                          item.id,
                          selected
                            ? "skip"
                            : (item.allowedActions.find((a) => a !== "skip") ?? "skip"),
                        )
                      }
                    />
                  </td>
                  <td>{item.title}</td>
                  <td>
                    <select
                      aria-label={`决策 ${item.displayPath}`}
                      value={decision}
                      onChange={(e) =>
                        setDecision(
                          batchId,
                          item.id,
                          e.target.value as Exclude<BatchItem["decision"], null>,
                        )
                      }
                    >
                      {item.allowedActions.map((action) => (
                        <option key={action} value={action}>
                          {action}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>{item.state}</td>
                  <td className="batch-secondary-column">{item.displayPath}</td>
                  <td className="batch-secondary-column">{item.sizeBytes}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {items.hasNextPage ? (
        <button
          className="button button-secondary"
          onClick={() => void items.fetchNextPage()}
          type="button"
        >
          加载更多
        </button>
      ) : null}
      <div className="dialog-actions">
        <button className="button button-secondary" onClick={onCancel} type="button">
          取消
        </button>
        <button
          className="button button-primary"
          onClick={onConfirm}
          disabled={
            pending ||
            (batch.data?.state !== undefined && batch.data.state !== "awaiting_confirmation")
          }
          type="button"
        >
          {pending ? "正在确认…" : "确认导入"}
        </button>
      </div>
    </div>
  );
}
