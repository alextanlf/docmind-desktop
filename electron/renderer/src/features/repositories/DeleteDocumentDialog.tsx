import { AlertTriangle } from "lucide-react";
import { useRef, useState } from "react";
import type { DocumentDetail } from "../../../../shared/contracts";
import { appQueryClient } from "../../app/query-client";
import { Modal } from "../../components/Modal";
import { clientErrorMessage } from "../settings/settings.queries";
import { repositoryKeys } from "./repository.queries";

type DeleteDocumentDialogProps = {
  document: DocumentDetail;
  repositoryName: string;
  onClose: () => void;
  onDeleted?: () => void;
};

export function DeleteDocumentDialog({
  document,
  repositoryName,
  onClose,
  onDeleted,
}: DeleteDocumentDialogProps) {
  const [confirmation, setConfirmation] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const closeRef = useRef<HTMLButtonElement>(null);
  const confirmed = confirmation === document.title;
  async function remove() {
    if (!confirmed) return;
    setPending(true);
    setError("");
    try {
      await window.docmind.documents.delete(document.id, true);
      appQueryClient.setQueryData<DocumentDetail[]>(
        repositoryKeys.documents(document.repositoryId),
        (documents) => documents?.filter((item) => item.id !== document.id),
      );
      await Promise.all([
        appQueryClient.invalidateQueries({
          queryKey: repositoryKeys.documents(document.repositoryId),
        }),
        appQueryClient.invalidateQueries({ queryKey: repositoryKeys.root }),
      ]);
      onDeleted?.();
      onClose();
    } catch (cause) {
      setError(clientErrorMessage(cause));
    } finally {
      setPending(false);
    }
  }
  return (
    <Modal
      className="confirm-dialog"
      initialFocusRef={closeRef}
      labelledBy="delete-document-title"
      onEscape={pending ? undefined : onClose}
    >
      <div className="dialog-title-row">
        <h3 id="delete-document-title">
          <AlertTriangle aria-hidden="true" size={18} />
          删除文档
        </h3>
        <button
          aria-label="关闭删除确认窗口"
          className="tree-icon-button"
          disabled={pending}
          onClick={onClose}
          ref={closeRef}
          title="关闭删除确认窗口"
          type="button"
        >
          ×
        </button>
      </div>
      <p>
        将从「{repositoryName}」删除「{document.title}」，此操作无法撤销。
      </p>
      <label className="dialog-field">
        <span>输入文档标题以确认</span>
        <input
          aria-label="输入文档标题以确认"
          disabled={pending}
          onChange={(event) => setConfirmation(event.target.value)}
          value={confirmation}
        />
      </label>
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
          disabled={!confirmed || pending}
          onClick={() => void remove()}
          type="button"
        >
          {pending ? "删除中…" : "删除文档"}
        </button>
      </div>
    </Modal>
  );
}
