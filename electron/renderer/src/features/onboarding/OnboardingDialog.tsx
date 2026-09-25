import { AlertTriangle, Check, LoaderCircle, RefreshCw } from "lucide-react";
import { useState } from "react";
import { Modal } from "../../components/Modal";
import { ModelSettingsForm } from "../settings/ModelSettingsForm";
import { useSettingsQuery } from "../settings/settings.queries";

export function OnboardingDialog({ onComplete }: { onComplete: () => void }) {
  const settings = useSettingsQuery();
  const [modelConnected, setModelConnected] = useState(false);

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

  return (
    <Modal labelledBy="onboarding-title" className="onboarding-dialog">
      <header className="onboarding-header">
        <div>
          <h1 id="onboarding-title">开始使用 DocMind</h1>
          <p>只需连接模型；语雀和飞书绑定都可以稍后在设置中完成</p>
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
        <span />
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
