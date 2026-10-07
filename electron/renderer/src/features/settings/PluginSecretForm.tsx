import { ExternalLink, LoaderCircle, ShieldCheck, Unlink } from "lucide-react";
import { useState } from "react";
import type { PluginManifest } from "../../../../shared/contracts";
import { StatusBadge } from "../../components/StatusBadge";
import {
  clientErrorMessage,
  useDeleteRemoteCredentialMutation,
  useSaveRemoteCredentialMutation,
  useTestRemoteCredentialMutation,
} from "./settings.queries";

/**
 * The secret-credential form for a plugin that needs one (an API token, an app
 * secret, a bot webhook URL).
 *
 * Everything on screen comes from the plugin's own manifest — label,
 * placeholder, help link, and the copy explaining the trade-off. A
 * URL-shaped credential must not be labelled "… Token", and a generic string
 * would have to name vendors to stay accurate for a third-party plugin.
 */
export function PluginSecretForm({ plugin }: { plugin: PluginManifest }) {
  const save = useSaveRemoteCredentialMutation(plugin.provider, plugin.channel);
  const test = useTestRemoteCredentialMutation(plugin.provider, plugin.channel);
  const remove = useDeleteRemoteCredentialMutation(plugin.provider, plugin.channel);
  const [secret, setSecret] = useState("");
  const [message, setMessage] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const busy = save.isPending || test.isPending || remove.isPending;
  const verified = plugin.state === "verified";

  async function saveAndVerify() {
    setMessage(null);
    try {
      if (secret.trim()) await save.mutateAsync({ secret: secret.trim() });
      const result = await test.mutateAsync();
      setSecret("");
      setMessage({ tone: "success", text: result.message });
    } catch (error) {
      setMessage({ tone: "error", text: clientErrorMessage(error) });
    }
  }

  async function removeBinding() {
    setMessage(null);
    try {
      await remove.mutateAsync();
      setSecret("");
      setMessage({ tone: "success", text: `已解除${plugin.label}绑定` });
    } catch (error) {
      setMessage({ tone: "error", text: clientErrorMessage(error) });
    }
  }

  return (
    <div className="plugin-form">
      <div className="status-title-line">
        <strong>{plugin.accountLabel ?? plugin.label}</strong>
        <StatusBadge
          label={verified ? "使用中" : plugin.configured ? "待验证" : "未绑定"}
          tone={verified ? "success" : plugin.configured ? "pending" : "neutral"}
        />
      </div>
      <label className="connection-field">
        <span>{plugin.label}</span>
        <input
          aria-label={plugin.label}
          autoComplete="off"
          disabled={busy}
          onChange={(event) => setSecret(event.target.value)}
          placeholder={
            plugin.configured
              ? "已保存，留空则保留"
              : (plugin.secretPlaceholder ?? "输入访问凭据")
          }
          type="password"
          value={secret}
        />
      </label>
      <div className="connection-actions">
        <button
          className="button button-primary"
          disabled={busy || (!secret.trim() && !plugin.configured)}
          onClick={() => void saveAndVerify()}
          type="button"
        >
          {busy ? (
            <LoaderCircle aria-hidden="true" className="spin" size={16} />
          ) : (
            <ShieldCheck aria-hidden="true" size={16} />
          )}
          {plugin.configured ? "验证并启用" : "保存并验证"}
        </button>
        {plugin.configured ? (
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
        {plugin.helpUrl ? (
          <a
            className="connection-help-link"
            href={plugin.helpUrl}
            onClick={(event) => {
              event.preventDefault();
              void window.docmind.shell.openExternal(plugin.helpUrl!);
            }}
          >
            {plugin.helpLabel ?? "获取凭据"}
            <ExternalLink aria-hidden="true" size={14} />
          </a>
        ) : null}
      </div>
      {message ? (
        <p className="form-message" role={message.tone === "error" ? "alert" : "status"}>
          {message.text}
        </p>
      ) : null}
    </div>
  );
}