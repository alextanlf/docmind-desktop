import { AlertTriangle } from "lucide-react";
import { useRef, useState } from "react";
import { Modal } from "../../components/Modal";
import { clientErrorMessage } from "../settings/settings.queries";
import { usePurgeDocumentMutation } from "./repository.queries";

/**
 * 一篇准备永久删除的文档。
 *
 * 只取用得上的字段而不是整个 `DocumentDetail`：这份列表（设置页的「已归档的文档」）
 * 只有归档摘要，没有正文 —— 而对话框不该假装自己有。
 */
export type PurgeTarget = {
  id: string;
  repositoryId: string;
  title: string;
  remoteId: string | null;
};

type PurgeDocumentDialogProps = {
  document: PurgeTarget;
  repositoryName: string;
  onClose: () => void;
  onPurged?: () => void;
};

/**
 * 永久删除一篇（已归档的）文档。
 *
 * **打字确认留在这里，而不是留在归档上**：这是唯一不可撤销的动作 —— 正文、分块、
 * 向量、版本历史、图谱节点与磁盘目录一起删；远端文档还可能把语雀/飞书上的原文也删掉。
 * 从前这道门加在"删除"上，而那时的"删除"是可恢复的本地移除，于是摩擦和不可逆性正好
 * 反了：可撤销的动作要打字，真的删远端只要勾一个框。
 *
 * 远端文档必须让用户**明确选一次**要不要连远端原文一起删，而不是替他选。
 */
export function PurgeDocumentDialog({
  document,
  repositoryName,
  onClose,
  onPurged,
}: PurgeDocumentDialogProps) {
  const [confirmation, setConfirmation] = useState("");
  const [alsoRemote, setAlsoRemote] = useState(false);
  const [error, setError] = useState("");
  const closeRef = useRef<HTMLButtonElement>(null);
  const purge = usePurgeDocumentMutation();
  const pending = purge.isPending;
  const confirmed = confirmation === document.title;
  // 只有从远端同步进来的文档才有远端原文可删。
  const remoteBound = Boolean(document.remoteId);
  const destructive = remoteBound && alsoRemote;

  async function remove() {
    if (!confirmed) return;
    setError("");
    try {
      await purge.mutateAsync({ documentId: document.id, alsoRemote: destructive });
      onPurged?.();
      onClose();
    } catch (cause) {
      setError(clientErrorMessage(cause));
    }
  }

  return (
    <Modal
      className="confirm-dialog"
      initialFocusRef={closeRef}
      labelledBy="purge-document-title"
      onEscape={pending ? undefined : onClose}
    >
      <div className="dialog-title-row">
        <h3 id="purge-document-title">
          <AlertTriangle aria-hidden="true" size={18} />
          永久删除文档
        </h3>
        <button
          aria-label="关闭永久删除确认窗口"
          className="tree-icon-button"
          disabled={pending}
          onClick={onClose}
          ref={closeRef}
          title="关闭永久删除确认窗口"
          type="button"
        >
          ×
        </button>
      </div>
      <p>
        将永久删除「{repositoryName}」里的「{document.title}」及其正文、索引与版本历史。
        此操作无法撤销。
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
          {pending ? "处理中…" : destructive ? "连远端一起删除" : "永久删除"}
        </button>
      </div>
    </Modal>
  );
}
