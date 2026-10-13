import { RefreshCw, RotateCcw } from "lucide-react";
import { useState } from "react";
import { clientErrorMessage } from "../settings/settings.queries";
import {
  useRemovedDocumentsQuery,
  useRestoreRemovedDocumentMutation,
  useSyncRepositoryMutation,
} from "./repository.queries";

/**
 * 已移出 DocMind 的远端文档。
 *
 * 恢复分两件事：后端让这篇文档"允许再次被同步"（删移出记录 + 清掉会挡住重新导入
 * 的陈旧同步快照），内容则由紧随其后的同步重新下载并重建索引。所以这里恢复成功后
 * 直接触发一次同步 —— 否则用户点了「恢复」会发现什么都没发生。
 *
 * 入口必须长在被移除的地方（知识库展开后的文档列表下方）：远端内容一直在，界面上
 * 没有线索的话用户找不回来。
 */
export function RemovedDocuments({ repositoryId }: { repositoryId: string }) {
  const removed = useRemovedDocumentsQuery(repositoryId);
  const restore = useRestoreRemovedDocumentMutation(repositoryId);
  const sync = useSyncRepositoryMutation(repositoryId);
  const [error, setError] = useState("");
  const [pendingId, setPendingId] = useState<string | null>(null);

  if (removed.isPending || removed.isError || removed.data.length === 0) return null;
  return (
    <div className="removed-document-section">
      <div className="sidebar-section-title">
        <span>已移除的文档</span>
      </div>
      <ul className="removed-document-list">
        {removed.data.map((item) => (
          <li key={item.id}>
            <span title={item.title}>{item.title}</span>
            <button
              aria-label={`恢复 ${item.title}`}
              className="tree-retry"
              disabled={restore.isPending || sync.isPending}
              onClick={() => {
                setError("");
                setPendingId(item.id);
                restore.mutate(item.id, {
                  // 恢复后立刻同步：只删记录的话文档不会自己回来（内容要重新下载
                  // 并重建索引），用户会以为按钮坏了。
                  onSuccess: () =>
                    sync.mutate(undefined, {
                      onError: (cause) => setError(clientErrorMessage(cause)),
                    }),
                  onError: (cause) => setError(clientErrorMessage(cause)),
                  onSettled: () => setPendingId(null),
                });
              }}
              type="button"
            >
              <RotateCcw aria-hidden="true" size={13} />
              {pendingId === item.id ? "恢复中…" : "恢复"}
            </button>
          </li>
        ))}
      </ul>
      {error ? (
        <p className="tree-error" role="alert">
          {error}
        </p>
      ) : null}
      <p className="tree-message">
        <RefreshCw aria-hidden="true" size={11} /> 恢复会重新下载并重建索引。
      </p>
    </div>
  );
}
