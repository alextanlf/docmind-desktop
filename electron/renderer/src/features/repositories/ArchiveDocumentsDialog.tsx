import { Archive } from "lucide-react";
import { useRef } from "react";
import type { DocumentSummary } from "../../../../shared/contracts";
import { Modal } from "../../components/Modal";

type ArchiveDocumentsDialogProps = {
  documents: DocumentSummary[];
  repositoryName: string;
  pending: boolean;
  error: string;
  onClose: () => void;
  onConfirm: () => void;
};

/**
 * 归档确认框（单篇与批量共用）。
 *
 * 这里**刻意不要求输入标题**：归档是可撤销的 —— 内容、向量、版本历史、图谱都还在，
 * 恢复是即时的。重确认留给不可撤销的那件事（设置页里的永久删除，那条路仍然要求
 * 输入标题）。摩擦与不可逆性成比例，而不是一律拉满。
 *
 * 文案要说清"东西去哪了、怎么回来"：只说"已归档"会让用户以为内容被删了，或者找不
 * 回来。远端文档与本地文档都没有区别 —— 两者的恢复入口是同一个地方。
 */
export function ArchiveDocumentsDialog({
  documents,
  repositoryName,
  pending,
  error,
  onClose,
  onConfirm,
}: ArchiveDocumentsDialogProps) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const single = documents.length === 1;

  return (
    <Modal
      className="confirm-dialog"
      initialFocusRef={closeRef}
      labelledBy="archive-documents-title"
      onEscape={pending ? undefined : onClose}
    >
      <div className="dialog-title-row">
        <h3 id="archive-documents-title">
          <Archive aria-hidden="true" size={18} />
          {single ? "归档文档" : `归档 ${documents.length} 篇文档`}
        </h3>
        <button
          aria-label="关闭归档确认窗口"
          className="tree-icon-button"
          disabled={pending}
          onClick={onClose}
          ref={closeRef}
          title="关闭归档确认窗口"
          type="button"
        >
          ×
        </button>
      </div>
      <p>
        {single
          ? `将从「${repositoryName}」归档「${documents[0].title}」。`
          : `将从「${repositoryName}」归档所选的 ${documents.length} 篇文档。`}
      </p>
      <p className="confirm-dialog-hint">
        正文、索引与版本历史都会保留，只是不再出现在列表和搜索里；随时可以在「设置 →
        已归档的文档」里恢复。
      </p>
      {single ? null : (
        <ul className="confirm-dialog-list">
          {documents.map((document) => (
            <li key={document.id}>{document.title}</li>
          ))}
        </ul>
      )}
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
          className="button button-primary"
          disabled={pending}
          onClick={onConfirm}
          type="button"
        >
          {pending ? "归档中…" : single ? "归档" : `归档 ${documents.length} 篇`}
        </button>
      </div>
    </Modal>
  );
}
