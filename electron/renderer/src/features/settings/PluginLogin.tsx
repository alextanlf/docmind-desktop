import { ExternalLink, LoaderCircle, LogIn } from "lucide-react";
import { useEffect, useState } from "react";
import type { PluginManifest } from "../../../../shared/contracts";
import { appQueryClient } from "../../app/query-client";
import { StatusBadge } from "../../components/StatusBadge";
import { clientErrorMessage, settingsKeys, useRemoteStatusQuery } from "./settings.queries";

/**
 * The interactive-login entry for a plugin that has no stored secret — browser
 * login or OAuth, whichever the plugin implements behind the same two calls.
 *
 * Whether the "install browser" affordance appears, and which error code means
 * "the browser is missing", are both declared by the plugin. Matching a vendor
 * code here made this component wrong for every other plugin, including ones
 * nobody had written yet.
 */
export function PluginLogin({ plugin }: { plugin: PluginManifest }) {
  const query = useRemoteStatusQuery(plugin.provider);
  const [loggingIn, setLoggingIn] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [browserUnavailable, setBrowserUnavailable] = useState(false);
  const [installingBrowser, setInstallingBrowser] = useState(false);
  const [installMessage, setInstallMessage] = useState<string | null>(null);
  const canInstallBrowser = plugin.browserInstall;
  const browserUnavailableCode = plugin.browserUnavailableCode ?? null;
  const isBrowserMissing = (value: unknown): boolean =>
    browserUnavailableCode !== null &&
    (value as { code?: string }).code === browserUnavailableCode;

  useEffect(() => {
    // Logging in changes what repositories exist, so any open picker has to
    // refetch — otherwise the newly reachable knowledge bases stay invisible
    // until the user restarts.
    if (query.data?.loggedIn) {
      void appQueryClient.invalidateQueries({ queryKey: ["repositories"] });
    }
  }, [query.data?.loggedIn]);

  async function login() {
    setLoggingIn(true);
    setError(null);
    try {
      const result = await window.docmind.remote.login(plugin.provider);
      appQueryClient.setQueryData(settingsKeys.remote(plugin.provider), result);
      await appQueryClient.invalidateQueries({ queryKey: ["repositories"] });
      setBrowserUnavailable(false);
    } catch (loginError) {
      setBrowserUnavailable(isBrowserMissing(loginError));
      setError(clientErrorMessage(loginError));
    } finally {
      setLoggingIn(false);
    }
  }

  async function installBrowser() {
    setInstallingBrowser(true);
    setInstallMessage(null);
    try {
      const result = await window.docmind.remote.installBrowser(plugin.provider);
      setInstallMessage(result.message);
      setBrowserUnavailable(false);
      await query.refetch();
    } catch (installError) {
      setInstallMessage(clientErrorMessage(installError));
    } finally {
      setInstallingBrowser(false);
    }
  }

  if (query.isPending) {
    return <p className="muted-row">正在检查{plugin.label}登录状态…</p>;
  }

  if (query.isError) {
    const unavailable = isBrowserMissing(query.error);
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
    <div className="plugin-form">
      <div className="status-title-line">
        <strong>{query.data.accountLabel ?? `${plugin.label}账号`}</strong>
        <StatusBadge
          label={query.data.loggedIn ? "已登录" : "未登录"}
          tone={query.data.loggedIn ? "success" : "pending"}
        />
      </div>
      {error ? (
        <p className="error-copy" role="alert">
          {error}
        </p>
      ) : null}
      <div className="connection-actions">
        <button className="button button-secondary" disabled={loggingIn} onClick={login}>
          {loggingIn ? (
            <LoaderCircle aria-hidden="true" className="spin" size={16} />
          ) : query.data.loggedIn ? (
            <ExternalLink aria-hidden="true" size={16} />
          ) : (
            <LogIn aria-hidden="true" size={16} />
          )}
          {query.data.loggedIn ? `重新登录${plugin.label}` : `登录${plugin.label}`}
        </button>
        {browserUnavailable && canInstallBrowser ? (
          <button
            className="button button-secondary"
            disabled={installingBrowser}
            onClick={installBrowser}
          >
            {installingBrowser ? "正在安装…" : "安装浏览器"}
          </button>
        ) : null}
      </div>
      {installMessage ? (
        <p className="error-copy" role="status">
          {installMessage}
        </p>
      ) : null}
    </div>
  );
}