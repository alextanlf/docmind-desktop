import { AlertTriangle, ArrowLeft, ArrowRight, Check, LoaderCircle, RefreshCw } from "lucide-react";
import { useState } from "react";
import { Modal } from "../../components/Modal";
import { ModelSettingsForm } from "../settings/ModelSettingsForm";
import { YuqueStatus } from "../settings/YuqueStatus";
import { useSettingsQuery, useYuqueStatusQuery } from "../settings/settings.queries";

export function OnboardingDialog({ onComplete }: { onComplete: () => void }) {
  const settings = useSettingsQuery();
  const yuque = useYuqueStatusQuery();
  const [step, setStep] = useState<1 | 2>(1);
  const [modelConnected, setModelConnected] = useState(false);

  if (settings.isPending || yuque.isPending) {
    return (
      <Modal labelledBy="onboarding-loading-title" className="onboarding-loading">
        <LoaderCircle aria-hidden="true" className="spin" size={20} />
        <span id="onboarding-loading-title">正在检查首次设置…</span>
      </Modal>
    );
  }
  if (settings.isError || yuque.isError) {
    return (
      <Modal labelledBy="onboarding-error-title" className="confirm-dialog onboarding-error-dialog">
        <AlertTriangle aria-hidden="true" size={22} />
        <h1 id="onboarding-error-title">无法读取首次设置</h1>
        <p>本地服务暂不可用，请重新检查后继续。</p>
        <button
          className="button button-primary"
          onClick={() => void Promise.all([settings.refetch(), yuque.refetch()])}
        >
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
          <p>完成模型连接，可稍后再登录语雀</p>
        </div>
        <ol className="onboarding-steps" aria-label="设置步骤">
          <li className={step === 1 ? "is-active" : modelConnected ? "is-complete" : undefined}>
            <span>{modelConnected ? <Check aria-hidden="true" size={14} /> : "1"}</span>
            配置模型
          </li>
          <li className={step === 2 ? "is-active" : undefined}>
            <span>{yuque.data.loggedIn ? <Check aria-hidden="true" size={14} /> : "2"}</span>
            登录语雀
          </li>
        </ol>
      </header>
      <div className="onboarding-content">
        {step === 1 ? (
          <>
            <div className="onboarding-step-title">
              <span>步骤 1</span>
              <h2>配置模型</h2>
              <p>先保存设置，再验证模型服务是否可用。</p>
            </div>
            <ModelSettingsForm
              onConnectionInvalidated={() => setModelConnected(false)}
              onConnectionSuccess={() => setModelConnected(true)}
              settings={settings.data}
              testButtonLabel="测试模型连接"
            />
          </>
        ) : (
          <>
            <div className="onboarding-step-title">
              <span>步骤 2</span>
              <h2>登录语雀</h2>
              <p>应用会打开可见浏览器窗口，请在其中完成登录。</p>
            </div>
            <YuqueStatus loginLabel="打开语雀登录" />
          </>
        )}
      </div>
      <footer className="onboarding-footer">
        {step === 2 ? (
          <button className="button button-secondary" onClick={() => setStep(1)}>
            <ArrowLeft aria-hidden="true" size={16} />
            上一步
          </button>
        ) : (
          <span />
        )}
        {step === 1 ? (
          <button
            className="button button-primary"
            disabled={!modelConnected}
            onClick={() => setStep(2)}
          >
            下一步
            <ArrowRight aria-hidden="true" size={16} />
          </button>
        ) : (
          <div className="onboarding-footer-actions">
            <button className="button button-secondary" onClick={onComplete}>
              稍后登录
            </button>
            <button
              className="button button-primary"
              disabled={!yuque.data.loggedIn}
              onClick={onComplete}
            >
              <Check aria-hidden="true" size={16} />
              进入工作台
            </button>
          </div>
        )}
      </footer>
    </Modal>
  );
}
