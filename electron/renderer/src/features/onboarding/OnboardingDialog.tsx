import { AlertTriangle, Check, LoaderCircle, RefreshCw, SkipForward } from "lucide-react";
import { useState } from "react";
import { Modal } from "../../components/Modal";
import { ModelSettingsForm } from "../settings/ModelSettingsForm";
import { useSettingsQuery, useSkipModelSetupMutation } from "../settings/settings.queries";

export function OnboardingDialog({ onComplete }: { onComplete: () => void }) {
  const settings = useSettingsQuery();
  const skipModelSetup = useSkipModelSetupMutation();
  const [modelConnected, setModelConnected] = useState(false);
  const [confirmingSkip, setConfirmingSkip] = useState(false);

  async function skipSetup() {
    try {
      await skipModelSetup.mutateAsync();
    } catch {
      // Dismissing setup is a convenience: if persisting it fails, still let the
      // user into the workspace rather than trapping them in this dialog. The
      // dialog will reappear next launch.
    }
    onComplete();
  }

  if (settings.isPending) {
    return (
      <Modal labelledBy="onboarding-loading-title" className="onboarding-loading">
        <LoaderCircle aria-hidden="true" className="spin" size={20} />
        <span id="onboarding-loading-title">正在检查首次设置…</span>
      </Modal>
    );
  }
  if (settings.isError) {
    return (
      <Modal labelledBy="onboarding-error-title" className="confirm-dialog onboarding-error-dialog">
        <AlertTriangle aria-hidden="true" size={22} />
        <h1 id="onboarding-error-title">无法读取首次设置</h1>
        <p>本地服务暂不可用，请重新检查后继续。</p>
        <button className="button button-primary" onClick={() => void settings.refetch()}>
          <RefreshCw aria-hidden="true" size={16} />
          重新检查设置
        </button>
      </Modal>
    );
  }

  if (confirmingSkip) {
    return (
      <Modal labelledBy="onboarding-skip-title" className="confirm-dialog onboarding-error-dialog">
        <AlertTriangle aria-hidden="true" size={22} />
        <h1 id="onboarding-skip-title">跳过模型配置？</h1>
        <p>
          未配置模型时，问答与摘要功能不可用；文档导入、解析和本地检索仍可正常使用。
          之后可随时在「设置 - 模型」中补上。
        </p>
        <div className="form-actions">
          <button className="button button-secondary" onClick={() => setConfirmingSkip(false)} type="button">
            返回配置
          </button>
          <button
            className="button button-primary"
            disabled={skipModelSetup.isPending}
            onClick={() => void skipSetup()}
            type="button"
          >
            {skipModelSetup.isPending ? (
              <LoaderCircle aria-hidden="true" className="spin" size={16} />
            ) : (
              <SkipForward aria-hidden="true" size={16} />
            )}
            跳过并进入
          </button>
        </div>
      </Modal>
    );
  }

  return (
    <Modal labelledBy="onboarding-title" className="onboarding-dialog">
      <header className="onboarding-header">
        <div>
          <h1 id="onboarding-title">开始使用 DocMind</h1>
          <p>只需连接模型；远程知识库和飞书绑定都可以稍后在设置中完成</p>
        </div>
        <ol className="onboarding-steps" aria-label="设置步骤">
          <li className={modelConnected ? "is-complete" : "is-active"}>
            <span>{modelConnected ? <Check aria-hidden="true" size={14} /> : "1"}</span>
            配置模型
          </li>
        </ol>
      </header>
      <div className="onboarding-content">
        <div className="onboarding-step-title">
          <span>步骤 1</span>
          <h2>配置模型</h2>
          <p>先保存设置并验证模型服务，成功后即可进入工作台。</p>
        </div>
        <ModelSettingsForm
          onConnectionInvalidated={() => setModelConnected(false)}
          onConnectionSuccess={() => setModelConnected(true)}
          settings={settings.data}
          testButtonLabel="测试模型连接"
        />
      </div>
      <footer className="onboarding-footer">
        <button
          className="button button-secondary"
          onClick={() => setConfirmingSkip(true)}
          type="button"
        >
          <SkipForward aria-hidden="true" size={16} />
          跳过
        </button>
        <button
          className="button button-primary"
          disabled={!modelConnected}
          onClick={onComplete}
          type="button"
        >
          <Check aria-hidden="true" size={16} />
          进入工作台
        </button>
      </footer>
    </Modal>
  );
}
