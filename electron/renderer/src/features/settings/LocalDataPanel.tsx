import { LoaderCircle, Trash2, X } from "lucide-react";
import { useRef, useState } from "react";
import type { SettingsView } from "../../../../shared/contracts";
import { appQueryClient } from "../../app/query-client";
import { IconButton } from "../../components/IconButton";
import { Modal } from "../../components/Modal";
import { clientErrorMessage, settingsKeys } from "./settings.queries";

/**
 * 本地数据：数据目录在哪，以及失败截图的清理。
 *
 * 这一块原来是一个独立分区（「本地数据与诊断」）。它只有一个只读路径和一个删除
 * 按钮，占一个顶级导航项太重；而它讲的事情本来属于「本地运行与路由」——数据存在
 * 本机哪里、本机留了什么，就是本地运行的一部分。分区撤销，内容并进那一页。
 *
 * 失败截图是语雀自动化失败时留下的排障产物（png + json + html），界面上看不到、
 * 也导出不了，所以这里只提供「有多少、清掉」两件事。
 */
export function LocalDataPanel({ settings }: { settings: SettingsView }) {
  const [confirming, setConfirming] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [savingFigureOcr, setSavingFigureOcr] = useState(false);
  const [savingFigureDescription, setSavingFigureDescription] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  async function toggleFigureDescription(enabled: boolean) {
    setSavingFigureDescription(true);
    setError(null);
    try {
      await window.docmind.settings.setFigureDescription(enabled);
      await appQueryClient.refetchQueries({ queryKey: settingsKeys.root });
    } catch (toggleError) {
      setError(clientErrorMessage(toggleError));
    } finally {
      setSavingFigureDescription(false);
    }
  }

  async function toggleFigureOcr(enabled: boolean) {
    setSavingFigureOcr(true);
    setError(null);
    try {
      await window.docmind.settings.setFigureOcr(enabled);
      await appQueryClient.refetchQueries({ queryKey: settingsKeys.root });
    } catch (toggleError) {
      setError(clientErrorMessage(toggleError));
    } finally {
      setSavingFigureOcr(false);
    }
  }

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
        <div className="settings-toggle-row">
          <div>
            <span className="field-label" id="figure-ocr-label">
              识别图片里的文字
            </span>
            <p className="field-hint">
              导入时识别 PDF、Word、网页里图片上的文字，让它能被搜到。关掉会快很多，
              但整页扫描的幻灯片和扫描书将无法检索 —— 它们没有文字层，识别是唯一的文字来源。
              已导入的文档不受影响。
            </p>
          </div>
          <label className="settings-toggle">
            <input
              aria-labelledby="figure-ocr-label"
              checked={settings.figureOcr}
              disabled={savingFigureOcr}
              onChange={(event) => void toggleFigureOcr(event.target.checked)}
              type="checkbox"
            />
            {/* 状态文字只是视觉补充，不参与可访问名 —— 挂在它上面会让名字随状态变化。 */}
            <span aria-hidden="true">{settings.figureOcr ? "已开启" : "已关闭"}</span>
          </label>
        </div>
        <div className="settings-toggle-row">
          <div>
            <span className="field-label" id="figure-description-label">
              用视觉模型描述图表
            </span>
            <p className="field-hint">
              图里没有文字时（状态转移图、曲线图、示意图），用模型看图并生成一句描述。
              文字识别读不出这类图，只有模型能。开启后这些图片会被发送到你在用的模型服务商，
              请确认这对你的资料是可接受的。只对没有文字的图发起。
            </p>
          </div>
          <label className="settings-toggle">
            <input
              aria-labelledby="figure-description-label"
              checked={settings.figureDescription}
              disabled={savingFigureDescription}
              onChange={(event) => void toggleFigureDescription(event.target.checked)}
              type="checkbox"
            />
            <span aria-hidden="true">{settings.figureDescription ? "已开启" : "已关闭"}</span>
          </label>
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
            将删除 {settings.screenshotCount}{" "}
            张远程知识库失败诊断截图，不影响文档、索引或其他本地数据。
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
