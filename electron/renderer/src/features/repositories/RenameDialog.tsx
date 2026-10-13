import { useRef, useState } from "react";
import { Modal } from "../../components/Modal";
import { clientErrorMessage } from "../settings/settings.queries";

export type RenameKind = "repository" | "document";

type RenameDialogProps = {
  kind: RenameKind;
  /** 现在的名字；对话框第一次渲染时用它做输入框初值。 */
  initialValue: string;
  /**
   * 这个条目绑定的远端来源能不能改名。
   *
   * `false` 时对话框**先说清楚**再让用户改：不然用户改完发现远端没变，会以为是自己
   * 操作错了。`true` 也可能落空（语雀网页会话不支持改知识库名），所以成功后仍然
   * 按实际结果再说一次。
   */
  remoteRenameSupported: boolean;
  /**
   * 执行改名。成功返回 `null`；改名只在本地生效时返回要展示的提示语
   * （文案住在前端，后端只送回 `scope` 这个原因码）。
   */
  onSubmit: (value: string) => Promise<string | null>;
  onClose: () => void;
};

/**
 * 改知识库名 / 改文档标题（单篇共用）。
 *
 * 用对话框而不是行内编辑：知识库行里已经有同步、移除两个按钮，文档行里是"勾选框 +
 * 标题按钮"，再塞一个输入框会让两种列表都变形，而且行内输入框在树的缩进里很难用。
 *
 * **改名成功不等于改名完整。** provider 改不了远端名字时（飞书没有重命名知识库的
 * 接口，语雀网页会话也没有），本地名字会变、远端不会 —— 而过一会儿同步再把远端
 * 旧名字拉回来。所以这种情况下对话框不关闭，而是原地把这件事说清楚。报错是错的
 * （名字确实改了），静默关闭也是错的（用户以为远端也改了）。
 */
export function RenameDialog({
  kind,
  initialValue,
  remoteRenameSupported,
  onSubmit,
  onClose,
}: RenameDialogProps) {
  const [value, setValue] = useState(initialValue);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const labels =
    kind === "repository"
      ? {
          title: "重命名知识库",
          field: "知识库名称",
          submit: "保存名称",
          close: "关闭重命名窗口",
          unchanged: "名字没有变，没有发出任何修改。",
        }
      : {
          title: "重命名文档",
          field: "文档标题",
          submit: "保存标题",
          close: "关闭重命名窗口",
          unchanged: "标题没有变，没有发出任何修改。",
        };

  const trimmed = value.trim();

  async function submit() {
    if (!trimmed || pending) return;
    setError("");
    setPending(true);
    try {
      const outcome = await onSubmit(trimmed);
      // 改名完整（远端也改了）就没什么要说的，直接关掉；只在本地生效时留在原地
      // 把话说清楚（名字确实变了，所以那不是错误）。
      if (outcome === null) onClose();
      else setNotice(outcome);
    } catch (cause) {
      setError(clientErrorMessage(cause));
    } finally {
      setPending(false);
    }
  }

  return (
    <Modal
      className="confirm-dialog"
      initialFocusRef={inputRef}
      labelledBy="rename-dialog-title"
      onEscape={pending ? undefined : onClose}
    >
      <div className="dialog-title-row">
        <h3 id="rename-dialog-title">{labels.title}</h3>
        <button
          aria-label={labels.close}
          className="tree-icon-button"
          disabled={pending}
          onClick={onClose}
          title={labels.close}
          type="button"
        >
          ×
        </button>
      </div>
      {notice ? (
        // 改名已经落地（本地那一半），所以这里不是错误而是一条结果说明；输入框与
        // 保存按钮一起收起来，免得用户以为还要再点一次。
        <p className="confirm-dialog-hint" role="status">
          {notice}
        </p>
      ) : (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <label htmlFor="rename-dialog-input">{labels.field}</label>
          <input
            autoComplete="off"
            id="rename-dialog-input"
            maxLength={kind === "repository" ? 120 : 240}
            onChange={(event) => setValue(event.target.value)}
            ref={inputRef}
            value={value}
          />
          {remoteRenameSupported ? null : (
            <p className="confirm-dialog-hint">
              {kind === "repository"
                ? "这个来源不支持在远端改名：名字只在 DocMind 里生效，之后同步可能把它改回远端的名字。"
                : "这个来源不支持只改标题：名字只在 DocMind 里生效，之后同步可能把它改回远端的标题。"}
            </p>
          )}
          {trimmed === initialValue.trim() ? (
            <p className="confirm-dialog-hint">{labels.unchanged}</p>
          ) : null}
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
              disabled={pending || !trimmed || trimmed === initialValue.trim()}
              type="submit"
            >
              {pending ? "保存中…" : labels.submit}
            </button>
          </div>
        </form>
      )}
      {notice ? (
        <div className="dialog-actions">
          <button className="button button-primary" onClick={onClose} type="button">
            知道了
          </button>
        </div>
      ) : null}
    </Modal>
  );
}
