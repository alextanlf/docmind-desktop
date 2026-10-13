import { AlertTriangle } from "lucide-react";
import { useRef } from "react";
import type { DocumentSummary } from "../../../../shared/contracts";
import { Modal } from "../../components/Modal";

type RemoveDocumentsDialogProps = {
  documents: DocumentSummary[];
  repositoryName: string;
  pending: boolean;
  error: string;
  onClose: () => void;
  onConfirm: () => void;
};

/**
 * 批量移除的确认框。
 *
 * 批量操作没法像单篇那样"输入标题以确认"，所以这里的责任全在文案上：必须逐项
 * 说清**哪些**内容会被删、远端会不会被动。尤其是本地文档 —— 它们没有"恢复"这条
 * 退路，而远端文档有。两者混在同一批里时，只报一个总数就是在骗人。
 */
export function RemoveDocumentsDialog({
  documents,
  repositoryName,
  pending,
  error,
  onClose,
  onConfirm,
}: RemoveDocumentsDialogProps) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const remoteCount = documents.filter((document) => document.remoteId).length;
  const localCount = documents.length - remoteCount;

  return (
    <Modal
      className="confirm-dialog"
      initialFocusRef={closeRef}
      labelledBy="remove-documents-title"
      onEscape={pending ? undefined : onClose}
    >
      <div className="dialog-title-row">
        <h3 id="remove-documents-title">
          <AlertTriangle aria-hidden="true" size={18} />
          移除 {documents.length} 篇文档
        </h3>
        <button
          aria-label="关闭移除确认窗口"
          className="tree-icon-button"
          disabled={pending}
          onClick={onClose}
          ref={closeRef}
          title="关闭移除确认窗口"
          type="button"
        >
          ×
        </button>
      </div>
      <p>
        将从「{repositoryName}」移除所选 {documents.length} 篇文档的本地内容与索引。
      </p>
      {remoteCount > 0 ? (
        <p className="confirm-dialog-hint">
          其中 {remoteCount} 篇来自远端知识库，远端原文会保留，之后可以在「已移除的文档」里恢复。
        </p>
      ) : null}
      {localCount > 0 ? (
        <p className="editor-error" role="alert">
          其中 {localCount} 篇是本地文档，移除后无法恢复（磁盘上的原件不受影响）。
        </p>
      ) : null}
      <ul className="confirm-dialog-list">
        {documents.map((document) => (
          <li key={document.id}>{document.title}</li>
        ))}
      </ul>
      {error ? (
        <p className="editor-error" role="alert">
          {error}
        </p>
      ) : null}
      <div className="dialog-actions">
        <button
          className="button button-secondary"
          disabled={pending}
          onClick={onClose}
          type="button"
        >
          取消
        </button>
        <button
          className="button button-danger"
          disabled={pending}
          onClick={onConfirm}
          type="button"
        >
          {pending ? "移除中…" : `移除 ${documents.length} 篇`}
        </button>
      </div>
    </Modal>
  );
}
