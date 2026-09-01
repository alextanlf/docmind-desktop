import { ExternalLink, LoaderCircle, LogIn } from "lucide-react";
import { useState } from "react";
import { appQueryClient } from "../../app/query-client";
import { StatusBadge } from "../../components/StatusBadge";
import { clientErrorMessage, settingsKeys, useYuqueStatusQuery } from "./settings.queries";

export function YuqueStatus({ loginLabel }: { loginLabel?: string }) {
  const query = useYuqueStatusQuery();
  const [loggingIn, setLoggingIn] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function login() {
    setLoggingIn(true);
    setError(null);
    try {
      const result = await window.docmind.yuque.login();
      appQueryClient.setQueryData(settingsKeys.yuque, result);
    } catch (loginError) {
      setError(clientErrorMessage(loginError));
    } finally {
      setLoggingIn(false);
    }
  }

  if (query.isPending) return <p className="muted-row">正在检查语雀登录状态…</p>;
  if (query.isError) {
    return (
      <div className="inline-error" role="alert">
        <span>{clientErrorMessage(query.error)}</span>
        <button className="button button-secondary" onClick={() => query.refetch()}>
          重新检查
        </button>
      </div>
    );
  }

  return (
    <div className="status-row">
      <div className="status-copy">
        <div className="status-title-line">
          <strong>{query.data.accountLabel ?? "语雀账号"}</strong>
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
      </div>
      <button className="button button-secondary" disabled={loggingIn} onClick={login}>
        {loggingIn ? (
          <LoaderCircle aria-hidden="true" className="spin" size={16} />
        ) : query.data.loggedIn ? (
          <ExternalLink aria-hidden="true" size={16} />
        ) : (
          <LogIn aria-hidden="true" size={16} />
        )}
        {loginLabel ?? (query.data.loggedIn ? "重新登录" : "登录语雀")}
      </button>
    </div>
  );
}
