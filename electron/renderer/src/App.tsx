import { useState } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { AppProviders } from "./app/AppProviders";
import { ErrorBoundary } from "./app/ErrorBoundary";
import { Workspace } from "./app/Workspace";
import { OnboardingDialog } from "./features/onboarding/OnboardingDialog";
import { useSettingsQuery, useYuqueStatusQuery } from "./features/settings/settings.queries";

function ResolvedApp({ initialReady }: { initialReady: boolean }) {
  const [requiresOnboarding] = useState(!initialReady);
  const [onboardingCompleted, setOnboardingCompleted] = useState(false);

  return (
    <>
      <Workspace />
      {requiresOnboarding && !onboardingCompleted ? (
        <OnboardingDialog onComplete={() => setOnboardingCompleted(true)} />
      ) : null}
    </>
  );
}

function AppContent() {
  const settings = useSettingsQuery();
  const yuque = useYuqueStatusQuery();

  if (settings.isError || yuque.isError) {
    return (
      <>
        <Workspace />
        <div className="dialog-backdrop">
          <section
            aria-labelledby="startup-error-title"
            aria-modal="true"
            className="confirm-dialog onboarding-error-dialog"
            role="dialog"
          >
            <AlertTriangle aria-hidden="true" size={22} />
            <h1 id="startup-error-title">无法读取首次设置</h1>
            <p>本地服务暂不可用，请重新检查后继续。</p>
            <button
              className="button button-primary"
              onClick={() => void Promise.all([settings.refetch(), yuque.refetch()])}
            >
              <RefreshCw aria-hidden="true" size={16} />
              重新检查设置
            </button>
          </section>
        </div>
      </>
    );
  }
  if (settings.isPending || yuque.isPending) return <Workspace />;
  const ready = settings.data?.hasApiKey === true && yuque.data?.loggedIn === true;
  return <ResolvedApp initialReady={ready} />;
}

export default function App() {
  return (
    <ErrorBoundary>
      <AppProviders>
        <AppContent />
      </AppProviders>
    </ErrorBoundary>
  );
}
