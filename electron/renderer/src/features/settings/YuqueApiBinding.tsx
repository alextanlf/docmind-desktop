import { ExternalLink, LoaderCircle, ShieldCheck, Unlink } from "lucide-react";
import { useState } from "react";
import type { SettingsView } from "../../../../shared/contracts";
import { StatusBadge } from "../../components/StatusBadge";
import {
  clientErrorMessage,
  useSaveYuqueApiMutation,
  useTestYuqueApiMutation,
} from "./settings.queries";

export function YuqueApiBinding({ settings }: { settings: SettingsView }) {
  const save = useSaveYuqueApiMutation();
  const test = useTestYuqueApiMutation();
  const [token, setToken] = useState("");
  const [message, setMessage] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const binding = settings.yuqueApi;
  const busy = save.isPending || test.isPending;

  async function saveAndVerify() {
    setMessage(null);
    try {
      if (token.trim()) await save.mutateAsync({ token: token.trim() });
      const result = await test.mutateAsync();
      setToken("");
      setMessage({ tone: "success", text: result.message });
    } catch (error) {
      setMessage({ tone: "error", text: clientErrorMessage(error) });
    }
  }

  async function removeBinding() {
    setMessage(null);
    try {
      await save.mutateAsync({ token: "" });
      setToken("");
      setMessage({ tone: "success", text: "已解除语雀 API 绑定" });
    } catch (error) {
      setMessage({ tone: "error", text: clientErrorMessage(error) });
    }
  }

  return (
    <div className="connection-binding-form">
      <div className="status-title-line">
        <strong>{binding.label ?? "语雀 API"}</strong>
        <StatusBadge
          label={binding.active ? "使用中" : binding.configured ? "待验证" : "未绑定"}
          tone={binding.active ? "success" : binding.configured ? "pending" : "neutral"}
        />
      </div>
      <p>
        {binding.active
          ? "已通过 Token 连接，语雀读写优先使用 API"
          : "使用语雀个人访问令牌，可避免依赖网页结构"}
      </p>
      <label className="connection-field">
        <span>语雀 API Token</span>
        <input
          aria-label="语雀 API Token"
          autoComplete="off"
          disabled={busy}
          onChange={(event) => setToken(event.target.value)}
          placeholder={binding.configured ? "已保存，留空则保留" : "输入语雀个人访问令牌"}
          type="password"
          value={token}
        />
      </label>
      <div className="connection-actions">
        <button
          className="button button-primary"
          disabled={busy || (!token.trim() && !binding.configured)}
          onClick={() => void saveAndVerify()}
          type="button"
        >
          {busy ? (
            <LoaderCircle aria-hidden="true" className="spin" size={16} />
          ) : (
            <ShieldCheck aria-hidden="true" size={16} />
          )}
          {binding.configured ? "验证并启用" : "保存并验证"}
        </button>
        {binding.configured ? (
          <button
            className="button button-danger-quiet"
            disabled={busy}
            onClick={() => void removeBinding()}
            type="button"
          >
            <Unlink aria-hidden="true" size={16} />
            解除绑定
          </button>
        ) : null}
        <a
          className="connection-help-link"
          href="https://www.yuque.com/settings/tokens"
          onClick={(event) => {
            event.preventDefault();
            void window.docmind.shell.openExternal("https://www.yuque.com/settings/tokens");
          }}
        >
          获取 Token
          <ExternalLink aria-hidden="true" size={14} />
        </a>
      </div>
      {message ? (
        <p className="form-message" role={message.tone === "error" ? "alert" : "status"}>
          {message.text}
        </p>
      ) : null}
    </div>
  );
}
