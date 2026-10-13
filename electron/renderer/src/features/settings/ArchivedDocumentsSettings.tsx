import { Archive, RotateCcw, Trash2 } from "lucide-react";
import { useState } from "react";
import type { ArchivedDocument, RemovedDocument } from "../../../../shared/contracts";
import {
  useArchivedDocumentsQuery,
  useRemovedDocumentsQuery,
  useRestoreRemovedDocumentMutation,
  useUnarchiveDocumentMutation,
} from "../repositories/repository.queries";
import { PurgeDocumentDialog } from "../repositories/PurgeDocumentDialog";
import { clientErrorMessage } from "./settings.queries";

/**
 * 「已归档的文档」——归档内容唯一的恢复与永久删除入口。
 *
 * 为什么是设置页而不是文档列表：归档之后那些文档**不再出现在任何列表里**，把撤销
 * 入口留在原地意味着它跟着内容一起消失。放进设置页后，"我归档过什么、要不要放回去"
 * 是一个可以主动去翻的地方，而不是靠记性。
 *
 * 这里同时列出两种东西，因为它们对用户是同一件事（"我从 DocMind 拿掉过什么"），
 * 但恢复代价完全不同，所以文案必须分开说：
 *
 * - **已归档**：数据全在，恢复是即时的。
 * - **已移除（旧记录）**：文档行当时就被删了，恢复要重新下载并重建索引。
 */
export function ArchivedDocumentsSettings() {
  const archived = useArchivedDocumentsQuery();
  const removed = useRemovedDocumentsQuery();
  const unarchive = useUnarchiveDocumentMutation();
  const restore = useRestoreRemovedDocumentMutation();
  const [purgeTarget, setPurgeTarget] = useState<ArchivedDocument | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [error, setError] = useState("");

  const archivedItems = archived.data ?? [];
  const removedItems = removed.data ?? [];

  function restoreArchived(document: ArchivedDocument) {
    setError("");
    setPendingId(document.id);
    unarchive.mutate(document.id, {
      onError: (cause) => setError(clientErrorMessage(cause)),
      onSettled: () => setPendingId(null),
    });
  }

  function restoreRemoved(item: RemovedDocument) {
    setError("");
    setPendingId(item.id);
    restore.mutate(
      { repositoryId: item.repositoryId, removalId: item.id },
      {
        // 恢复只删掉移出记录；内容要重新下载并重建索引，所以紧接着触发那个库的同步 ——
        // 少了这一步，用户点了「恢复」会发现什么都没发生。
        onSuccess: () =>
          window.docmind.sync.trigger(item.repositoryId).catch((cause: unknown) => {
            setError(clientErrorMessage(cause));
          }),
        onError: (cause) => setError(clientErrorMessage(cause)),
        onSettled: () => setPendingId(null),
      },
    );
  }

  if (archived.isPending || removed.isPending) {
    return <p className="tree-message">正在读取已归档的文档…</p>;
  }
  if (archivedItems.length === 0 && removedItems.length === 0) {
    return (
      <p className="tree-message">
        还没有归档的文档。在知识库里归档一篇文档，它会出现在这里；归档期间正文与索引
        都保留着，随时可以恢复。
      </p>
    );
  }

  return (
    <div className="archived-documents">
      {archivedItems.length > 0 ? (
        <>
          <p className="settings-hint">
            归档的文档不再出现在列表与搜索里，但正文、索引与版本历史都还在 ——
            恢复是即时的。永久删除会把这些一起删掉，无法撤销。
          </p>
          <ul className="archived-document-list" aria-label="已归档的文档">
            {archivedItems.map((document) => (
              <li key={document.id}>
                <span className="archived-document-title" title={document.title}>
                  <Archive aria-hidden="true" size={13} />
                  {document.title}
                </span>
                <span className="archived-document-meta">
                  {document.repositoryName || "未知知识库"} ·{" "}
                  {new Date(document.archivedAt).toLocaleDateString()}
                </span>
                <span className="archived-document-actions">
                  <button
                    aria-label={`恢复 ${document.title}`}
                    className="tree-retry"
                    disabled={pendingId !== null}
                    onClick={() => restoreArchived(document)}
                    type="button"
                  >
                    <RotateCcw aria-hidden="true" size={13} />
                    {pendingId === document.id ? "恢复中…" : "恢复"}
                  </button>
                  <button
                    aria-label={`永久删除 ${document.title}`}
                    className="tree-retry"
                    disabled={pendingId !== null}
                    onClick={() => setPurgeTarget(document)}
                    type="button"
                  >
                    <Trash2 aria-hidden="true" size={13} />
                    永久删除
                  </button>
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {removedItems.length > 0 ? (
        <>
          <h3 className="sidebar-section-title">已移除、可重新同步的远端文档</h3>
          <p className="settings-hint">
            这些是更早版本的移除动作留下的记录：本地副本已经被删掉，恢复会重新下载并重建索引。
          </p>
          <ul className="archived-document-list" aria-label="已移除的远端文档">
            {removedItems.map((item) => (
              <li key={item.id}>
                <span className="archived-document-title" title={item.title}>
                  {item.title}
                </span>
                <span className="archived-document-meta">
                  {item.repositoryName || "未知知识库"}
                </span>
                <span className="archived-document-actions">
                  <button
                    aria-label={`恢复 ${item.title}`}
                    className="tree-retry"
                    disabled={pendingId !== null}
                    onClick={() => restoreRemoved(item)}
                    type="button"
                  >
                    <RotateCcw aria-hidden="true" size={13} />
                    {pendingId === item.id ? "恢复中…" : "恢复"}
                  </button>
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {error ? (
        <p className="editor-error" role="alert">
          {error}
        </p>
      ) : null}
      {purgeTarget ? (
        <PurgeDocumentDialog
          document={purgeTarget}
          onClose={() => setPurgeTarget(null)}
          repositoryName={purgeTarget.repositoryName || "知识库"}
        />
      ) : null}
    </div>
  );
}
