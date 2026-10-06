import { ExternalLink, LoaderCircle, ShieldCheck, Unlink } from "lucide-react";
import { useState } from "react";
import type { RemoteCredentialChannel } from "../../../../shared/contracts";
import { StatusBadge } from "../../components/StatusBadge";
import {
  clientErrorMessage,
  useDeleteRemoteCredentialMutation,
  useSaveRemoteCredentialMutation,
  useTestRemoteCredentialMutation,
} from "./settings.queries";

/**
 * Generic secret-credential form for any remote provider channel
 * (API token, app secret, ...). Driven entirely by the channel descriptor
 * returned by `GET /api/remote/providers/{provider}/credentials`.
 */
export function RemoteSecretChannel({ channel }: { channel: RemoteCredentialChannel }) {
  const save = useSaveRemoteCredentialMutation(channel.provider, channel.channel);
  const test = useTestRemoteCredentialMutation(channel.provider, channel.channel);
  const remove = useDeleteRemoteCredentialMutation(channel.provider, channel.channel);
  const [secret, setSecret] = useState("");
  const [message, setMessage] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const busy = save.isPending || test.isPending || remove.isPending;
  const verified = channel.state === "verified";
  // A channel whose secret is a URL (a bot webhook) would read as "… Token",
  // so the label comes from the channel and only gains a "Token" suffix when
  // the provider did not supply its own wording.
  const secretLabel = channel.label;

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
      setMessage({ tone: "success", text: `已解除${channel.label}绑定` });
    } catch (error) {
      setMessage({ tone: "error", text: clientErrorMessage(error) });
    }
  }

  return (
    <div className="connection-binding-form">
      <div className="status-title-line">
        <strong>{channel.accountLabel ?? channel.label}</strong>
        <StatusBadge
          label={verified ? "使用中" : channel.configured ? "待验证" : "未绑定"}
          tone={verified ? "success" : channel.configured ? "pending" : "neutral"}
        />
      </div>
      <p>
        {verified
          ? `已通过凭据连接，${channel.label}通道可用`
          : "保存凭据后需通过连接测试才会启用"}
      </p>
      <label className="connection-field">
        <span>{secretLabel}</span>
        <input
          aria-label={secretLabel}
          autoComplete="off"
          disabled={busy}
          onChange={(event) => setSecret(event.target.value)}
          placeholder={
            channel.configured
              ? "已保存，留空则保留"
              : (channel.secretPlaceholder ?? "输入访问凭据")
          }
          type="password"
          value={secret}
        />
      </label>
      <div className="connection-actions">
        <button
          className="button button-primary"
          disabled={busy || (!secret.trim() && !channel.configured)}
          onClick={() => void saveAndVerify()}
          type="button"
        >
          {busy ? (
            <LoaderCircle aria-hidden="true" className="spin" size={16} />
          ) : (
            <ShieldCheck aria-hidden="true" size={16} />
          )}
          {channel.configured ? "验证并启用" : "保存并验证"}
        </button>
        {channel.configured ? (
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
        {channel.helpUrl ? (
          <a
            className="connection-help-link"
            href={channel.helpUrl}
            onClick={(event) => {
              event.preventDefault();
              void window.docmind.shell.openExternal(channel.helpUrl!);
            }}
          >
            {channel.helpLabel ?? "获取凭据"}
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
