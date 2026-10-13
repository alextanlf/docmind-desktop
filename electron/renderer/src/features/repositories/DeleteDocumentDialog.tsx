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

/**
 * 移除 / 删除一篇文档。
 *
 * 两个动作在这里被刻意分开：
 *
 * * **移除**（默认）只清 DocMind 本地 —— 远端知识库里的原文保留。
 * * **连远端一起删除** 会真的删掉用户在语雀/飞书上的东西，不可撤销。
 *
 * 从前只有一个「删除文档」按钮，对远端文档它走的是后者，而文案只写「此操作无法
 * 撤销」—— 用户以为自己在本地清理，实际删掉了远端原文。远端文档必须让用户
 * 明确选一次，而不是替他选。
 */
export function DeleteDocumentDialog({
  document,
  repositoryName,
  onClose,
  onDeleted,
}: DeleteDocumentDialogProps) {
  const [confirmation, setConfirmation] = useState("");
  const [alsoRemote, setAlsoRemote] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const closeRef = useRef<HTMLButtonElement>(null);
  const confirmed = confirmation === document.title;
  // 只有从远端同步进来的文档才有远端原文可删。
  const remoteBound = Boolean(document.remoteId);

  async function remove() {
    if (!confirmed) return;
    setPending(true);
    setError("");
    try {
      if (remoteBound && alsoRemote) {
        await window.docmind.documents.deleteRemote(document.id, true);
      } else {
        await window.docmind.documents.remove(document.id, true);
      }
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

  const destructive = remoteBound && alsoRemote;
  return (
    <Modal
      className="confirm-dialog"
      initialFocusRef={closeRef}
      labelledBy="remove-document-title"
      onEscape={pending ? undefined : onClose}
    >
      <div className="dialog-title-row">
        <h3 id="remove-document-title">
          <AlertTriangle aria-hidden="true" size={18} />
          移除文档
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
        将从「{repositoryName}」移除「{document.title}」及其本地索引。
        {remoteBound ? null : "此操作无法撤销。"}
      </p>
      {remoteBound ? (
        <>
          <p className="confirm-dialog-hint">远端知识库里的原文会保留。</p>
          <label className="dialog-checkbox">
            <input
              checked={alsoRemote}
              disabled={pending}
              onChange={(event) => setAlsoRemote(event.target.checked)}
              type="checkbox"
            />
            <span>同时删除远端原文（不可撤销）</span>
          </label>
        </>
      ) : null}
      {destructive ? (
        <p className="editor-error" role="alert">
          远端原文将被永久删除，无法撤销。
        </p>
      ) : null}
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
          {pending ? "处理中…" : destructive ? "连远端一起删除" : "移除文档"}
        </button>
      </div>
    </Modal>
  );
}
