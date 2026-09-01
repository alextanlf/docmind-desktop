import { LoaderCircle, Trash2, X } from "lucide-react";
import { useRef, useState } from "react";
import type { SettingsView } from "../../../../shared/contracts";
import { appQueryClient } from "../../app/query-client";
import { IconButton } from "../../components/IconButton";
import { Modal } from "../../components/Modal";
import { clientErrorMessage, settingsKeys } from "./settings.queries";

export function DiagnosticsSection({ settings }: { settings: SettingsView }) {
  const [confirming, setConfirming] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  async function clearScreenshots() {
    setClearing(true);
    setError(null);
    try {
      await window.docmind.settings.clearDiagnostics();
      await appQueryClient.refetchQueries({ queryKey: settingsKeys.root });
      setConfirming(false);
    } catch (clearError) {
      setError(clientErrorMessage(clearError));
    } finally {
      setClearing(false);
    }
  }

  return (
    <>
      <div className="diagnostics-grid">
        <div>
          <span className="field-label">本地数据位置</span>
          <output className="data-path">{settings.dataPath}</output>
        </div>
        <div className="diagnostics-count">
          <span>失败截图 {settings.screenshotCount} 张</span>
          <button
            className="button button-danger-quiet"
            disabled={settings.screenshotCount === 0}
            onClick={() => setConfirming(true)}
          >
            <Trash2 aria-hidden="true" size={16} />
            清理失败截图
          </button>
        </div>
      </div>
      {confirming ? (
        <Modal
          backdropClassName="dialog-backdrop-nested"
          className="confirm-dialog"
          initialFocusRef={closeButtonRef}
          labelledBy="diagnostics-confirm-title"
          onEscape={() => setConfirming(false)}
        >
          <div className="dialog-title-row">
            <h3 id="diagnostics-confirm-title">确认清理失败截图</h3>
            <IconButton
              icon={<X aria-hidden="true" size={18} />}
              label="关闭确认窗口"
              onClick={() => setConfirming(false)}
              ref={closeButtonRef}
              size="small"
            />
          </div>
          <p>
            将删除 {settings.screenshotCount} 张语雀失败诊断截图，不影响文档、索引或其他本地数据。
          </p>
          {error ? (
            <p className="error-copy" role="alert">
              {error}
            </p>
          ) : null}
          <div className="dialog-actions">
            <button className="button button-secondary" onClick={() => setConfirming(false)}>
              取消
            </button>
            <button className="button button-danger" disabled={clearing} onClick={clearScreenshots}>
              {clearing ? (
                <LoaderCircle aria-hidden="true" className="spin" size={16} />
              ) : (
                <Trash2 aria-hidden="true" size={16} />
              )}
              确认清理
            </button>
          </div>
        </Modal>
      ) : null}
    </>
  );
}
