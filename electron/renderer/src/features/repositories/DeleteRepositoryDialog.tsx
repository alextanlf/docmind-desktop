import { AlertTriangle } from "lucide-react";
import { useRef, useState } from "react";
import type { Repository } from "../../../../shared/contracts";
import { Modal } from "../../components/Modal";
import { clientErrorMessage } from "../settings/settings.queries";
import { useRemoveRepositoryMutation } from "./repository.queries";

type DeleteRepositoryDialogProps = {
  repository: Repository;
  providerLabel: string;
  onClose: () => void;
  onRemoved?: () => void;
};

/**
 * 移除知识库的确认框。
 *
 * 用「移除」而不是「删除」：远端绑定的库在远端**什么都没删**，而这个框是用户
 * 唯一会读到的说明。对本地库则必须说清"无法撤销"—— 它没有任何找回的入口。
 *
 * 远端绑定的库额外要说两件事：远端内容不动，以及以后能在这里找回。少说第二件，
 * 用户会以为移除是永久性的而不敢点。
 */
export function DeleteRepositoryDialog({
  repository,
  providerLabel,
  onClose,
  onRemoved,
}: DeleteRepositoryDialogProps) {
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState("");
  const closeRef = useRef<HTMLButtonElement>(null);
  const remove = useRemoveRepositoryMutation();
  const confirmed = confirmation === repository.name;
  const remoteBound = Boolean(repository.provider);

  function submit() {
    if (!confirmed) return;
    setError("");
    remove.mutate(repository.id, {
      onSuccess: () => {
        onRemoved?.();
        onClose();
      },
      onError: (cause) => setError(clientErrorMessage(cause)),
    });
  }

  return (
    <Modal
      className="confirm-dialog"
      initialFocusRef={closeRef}
      labelledBy="remove-repository-title"
      onEscape={remove.isPending ? undefined : onClose}
    >
      <div className="dialog-title-row">
        <h3 id="remove-repository-title">
          <AlertTriangle aria-hidden="true" size={18} />
          移除知识库
        </h3>
        <button
          aria-label="关闭移除确认窗口"
          className="tree-icon-button"
          disabled={remove.isPending}
          onClick={onClose}
          ref={closeRef}
          title="关闭移除确认窗口"
          type="button"
        >
          ×
        </button>
      </div>
      <p>
        将从 DocMind 移除「{repository.name}」及其本地文档与索引。
        {remoteBound ? null : "此操作无法撤销。"}
      </p>
      {remoteBound ? (
        <p className="confirm-dialog-hint">
          {providerLabel}上的知识库与文档不会被删除。以后可以在「已移除的知识库」里找回，
          文档用「立即同步」重新拉取。
        </p>
      ) : null}
      <label className="dialog-field">
        <span>输入知识库名称以确认</span>
        <input
          aria-label="输入知识库名称以确认"
          disabled={remove.isPending}
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
          disabled={remove.isPending}
          onClick={onClose}
          type="button"
        >
          取消
        </button>
        <button
          className="button button-danger"
          disabled={!confirmed || remove.isPending}
          onClick={submit}
          type="button"
        >
          {remove.isPending ? "移除中…" : "移除知识库"}
        </button>
      </div>
    </Modal>
  );
}
