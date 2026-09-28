import { ExternalLink, LoaderCircle, LogIn } from "lucide-react";
import { useEffect, useState } from "react";
import type { RemoteProviderCapabilities } from "../../../../shared/contracts";
import { appQueryClient } from "../../app/query-client";
import { StatusBadge } from "../../components/StatusBadge";
import { clientErrorMessage, settingsKeys, useRemoteStatusQuery } from "./settings.queries";

export function RemoteStatus({
  provider = "yuque",
  displayName = "语雀",
  loginLabel,
  capabilities,
}: {
  provider?: string;
  displayName?: string;
  loginLabel?: string;
  capabilities?: RemoteProviderCapabilities;
}) {
  const query = useRemoteStatusQuery(provider);
  const [loggingIn, setLoggingIn] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [browserUnavailable, setBrowserUnavailable] = useState(false);
  const [installingBrowser, setInstallingBrowser] = useState(false);
  const [installMessage, setInstallMessage] = useState<string | null>(null);
  // Providers without the browser_install capability never offer the install
  // button; absent capability info keeps the historical behavior.
  const canInstallBrowser = capabilities?.browserInstall ?? true;

  useEffect(() => {
    if (query.data?.loggedIn) {
      void appQueryClient.invalidateQueries({ queryKey: ["repositories"] });
    }
  }, [query.data?.loggedIn]);

  async function login() {
    setLoggingIn(true);
    setError(null);
    try {
      const result = await window.docmind.remote.login(provider);
      appQueryClient.setQueryData(settingsKeys.remote(provider), result);
      await appQueryClient.invalidateQueries({ queryKey: ["repositories"] });
      setBrowserUnavailable(false);
    } catch (loginError) {
      setBrowserUnavailable((loginError as { code?: string }).code === "YUQUE_BROWSER_UNAVAILABLE");
      setError(clientErrorMessage(loginError));
    } finally {
      setLoggingIn(false);
    }
  }

  async function installBrowser() {
    setInstallingBrowser(true);
    setInstallMessage(null);
    try {
      const result = await window.docmind.remote.installBrowser(provider);
      setInstallMessage(result.message);
      setBrowserUnavailable(false);
      await query.refetch();
    } catch (installError) {
      setInstallMessage(clientErrorMessage(installError));
    } finally {
      setInstallingBrowser(false);
    }
  }

  if (query.isPending) return <p className="muted-row">正在检查{displayName}登录状态…</p>;
  if (query.isError) {
    const unavailable = (query.error as { code?: string }).code === "YUQUE_BROWSER_UNAVAILABLE";
    return (
      <div className="inline-error" role="alert">
        <span>{clientErrorMessage(query.error)}</span>
        {unavailable && canInstallBrowser ? (
          <button
            className="button button-secondary"
            disabled={installingBrowser}
            onClick={installBrowser}
          >
            {installingBrowser ? "正在安装…" : "安装浏览器"}
          </button>
        ) : (
          <button className="button button-secondary" onClick={() => query.refetch()}>
            重新检查
          </button>
        )}
        {installMessage ? (
          <p className="error-copy" role="status">
            {installMessage}
          </p>
        ) : null}
      </div>
    );
  }

  return (
    <div className="status-row">
      <div className="status-copy">
        <div className="status-title-line">
          <strong>{query.data.accountLabel ?? `${displayName}账号`}</strong>
          <StatusBadge
            label={query.data.loggedIn ? "已登录" : "未登录"}
            tone={query.data.loggedIn ? "success" : "pending"}
          />
        </div>
        <p>{query.data.loggedIn ? "可同步知识库与文档" : "将在可见浏览器窗口中完成登录"}</p>
        {error ? (
          <p className="error-copy" role="alert">
            {error}
          </p>
        ) : null}
        {browserUnavailable && canInstallBrowser ? (
          <button
            className="button button-secondary"
            disabled={installingBrowser}
            onClick={installBrowser}
          >
            {installingBrowser ? "正在安装…" : "安装浏览器"}
          </button>
        ) : null}
        {installMessage ? (
          <p className="error-copy" role="status">
            {installMessage}
          </p>
        ) : null}
      </div>
      <button className="button button-secondary" disabled={loggingIn} onClick={login}>
        {loggingIn ? (
          <LoaderCircle aria-hidden="true" className="spin" size={16} />
        ) : query.data.loggedIn ? (
          <ExternalLink aria-hidden="true" size={16} />
        ) : (
          <LogIn aria-hidden="true" size={16} />
        )}
        {loginLabel ?? (query.data.loggedIn ? "重新登录" : `登录${displayName}`)}
      </button>
    </div>
  );
}
