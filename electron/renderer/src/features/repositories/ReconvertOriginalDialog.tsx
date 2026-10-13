import { useRef } from "react";
import { AlertTriangle, ExternalLink } from "lucide-react";

import { Modal } from "../../components/Modal";

/**
 * 「重新转换」前的一次确认。
 *
 * 为什么要问：重新转换是拿原件重新解析、**整篇替换正文**。而"原件已更新"这个提示本身
 * 在鼓励用户去 Word/Preview 里改原件 —— 改完回来点重新转换，正是最容易顺手抹掉自己
 * 在应用内改过的东西的路径。
 *
 * 为什么不是"只在改过的时候才问"：后端没有"这篇被手工编辑过"的标记，而凭
 * "版本历史里有没有快照"去猜是错的（重新转换本身也会存快照）。宁可在每一次都问一句，
 * 也不要做一个会漏报的猜测 —— 漏报的那次正是丢内容的那次。
 */
export function ReconvertOriginalDialog({
  documentTitle,
  onConfirm,
  onClose,
  pending,
}: {
  documentTitle: string;
  onConfirm: () => void;
  onClose: () => void;
  pending: boolean;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  return (
    <Modal
      className="confirm-dialog"
      initialFocusRef={closeRef}
      labelledBy="reconvert-original-title"
      onEscape={pending ? undefined : onClose}
    >
      <div className="dialog-title-row">
        <h3 id="reconvert-original-title">
          <AlertTriangle aria-hidden="true" size={18} />
          重新转换
        </h3>
        <button
          aria-label="关闭重新转换确认窗口"
          className="tree-icon-button"
          disabled={pending}
          onClick={onClose}
          ref={closeRef}
          title="关闭重新转换确认窗口"
          type="button"
        >
          ×
        </button>
      </div>
      <p>
        将重新读取原件并替换「{documentTitle}」的正文。
        如果你在应用里改过正文，这次替换会覆盖那些改动 —— 覆盖前的正文会留在版本历史里。
      </p>
      <p className="confirm-dialog-hint">
        <ExternalLink aria-hidden="true" size={14} />
        想保留应用内的改动，请先取消，把改动整理进原件后再来。
      </p>
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
          {pending ? "处理中…" : "覆盖并重新转换"}
        </button>
      </div>
    </Modal>
  );
}
