import { useState } from "react";
import { LoaderCircle } from "lucide-react";
import { AppProviders } from "./app/AppProviders";
import { ErrorBoundary } from "./app/ErrorBoundary";
import { Workspace } from "./app/Workspace";
import { Modal } from "./components/Modal";
import { OnboardingDialog } from "./features/onboarding/OnboardingDialog";
import { SettingsLoadErrorDialog } from "./features/settings/SettingsLoadErrorDialog";
import { useSettingsQuery } from "./features/settings/settings.queries";

function ResolvedApp({ initialReady, setupSkipped }: { initialReady: boolean; setupSkipped: boolean }) {
  // Only the first snapshot decides whether setup is required. Re-reading
  // settings while the dialog is open would unmount it mid-edit: saving a model
  // flips hasApiKey to true, which would drop the dialog before the user could
  // press "进入工作台".
  const [requiresOnboarding] = useState(!initialReady && !setupSkipped);
  const [dismissed, setDismissed] = useState(false);

  return (
    <>
      <Workspace />
      {requiresOnboarding && !dismissed ? (
        <OnboardingDialog onComplete={() => setDismissed(true)} />
      ) : null}
    </>
  );
}

function AppContent() {
  const settings = useSettingsQuery();
  const startupPending = settings.isPending;

  if (settings.isError) {
    return (
      <>
        <Workspace />
        <SettingsLoadErrorDialog error={settings.error} onRetry={() => void settings.refetch()} />
      </>
    );
  }
  if (startupPending) {
    return (
      <>
        <Modal
          labelledBy="startup-loading-title"
          className="confirm-dialog onboarding-error-dialog"
        >
          <LoaderCircle aria-hidden="true" className="spin" size={22} />
          <h1 id="startup-loading-title">正在检查首次设置</h1>
          <p>正在读取模型设置，请稍候。</p>
        </Modal>
      </>
    );
  }
  const ready = settings.data?.hasApiKey === true;
  return <ResolvedApp initialReady={ready} setupSkipped={settings.data?.modelSetupSkipped === true} />;
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
