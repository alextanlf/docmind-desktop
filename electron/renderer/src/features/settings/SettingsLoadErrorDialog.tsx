import { AlertTriangle, RefreshCw, RotateCw } from "lucide-react";
import { useState } from "react";
import { Modal } from "../../components/Modal";
import { clientErrorMessage } from "../settings/ollama-errors";

/**
 * A renderer/backend contract mismatch is deterministic: refetching re-runs the
 * identical failing validation, so offering only "重新检查设置" traps the user in
 * a loop with no way out. `BACKEND_UNAVAILABLE` is excluded on purpose — it is
 * genuinely transient and retry is the correct response.
 */
const CONTRACT_MISMATCH = new Set(["BACKEND_PROTOCOL_ERROR"]);

function isContractMismatch(error: unknown): boolean {
  const code = (error as { code?: string } | null)?.code;
  return code !== undefined && CONTRACT_MISMATCH.has(code);
}

/**
 * Startup failure surface for the settings query. Retrying is the primary action
 * because most failures are transient (backend still booting); when the failure
 * is a contract mismatch we say so honestly and offer a restart, which is the
 * only thing that can actually change the outcome.
 */
export function SettingsLoadErrorDialog({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry: () => void;
}) {
  const [restarting, setRestarting] = useState(false);
  const mismatch = isContractMismatch(error);

  async function restart() {
    setRestarting(true);
    try {
      await window.docmind.app.restart();
    } catch {
      // Restart is best-effort; surface the retry path again rather than
      // leaving a disabled button behind.
      setRestarting(false);
    }
  }

  return (
    <Modal labelledBy="settings-error-title" className="confirm-dialog onboarding-error-dialog">
      <AlertTriangle aria-hidden="true" size={22} />
      <h1 id="settings-error-title">
        {mismatch ? "本地服务返回了无法识别的数据" : "无法读取首次设置"}
      </h1>
      <p>
        {mismatch
          ? "应用与本地服务的版本不一致，重试无法解决。请重启应用，若仍失败请更新到最新版本。"
          : "本地服务暂不可用，请重新检查后继续。"}
      </p>
      {mismatch ? (
        <div className="form-actions">
          <button className="button button-secondary" onClick={onRetry} type="button">
            <RefreshCw aria-hidden="true" size={16} />
            重新检查设置
          </button>
          <button
            className="button button-primary"
            disabled={restarting}
            onClick={() => void restart()}
            type="button"
          >
            <RotateCw aria-hidden="true" size={16} />
            {restarting ? "正在重启…" : "重启应用"}
          </button>
        </div>
      ) : (
        <button className="button button-primary" onClick={onRetry}>
          <RefreshCw aria-hidden="true" size={16} />
          重新检查设置
        </button>
      )}
      <p className="form-hint">{clientErrorMessage(error)}</p>
    </Modal>
  );
}