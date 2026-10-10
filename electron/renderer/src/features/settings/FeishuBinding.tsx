import { ExternalLink, Link2, LoaderCircle, Unlink } from "lucide-react";
import { useState } from "react";
import type { SettingsView } from "../../../../shared/contracts";
import { StatusBadge } from "../../components/StatusBadge";
import {
  clientErrorMessage,
  useSaveFeishuMutation,
  useTestFeishuMutation,
} from "./settings.queries";

export function FeishuBinding({ settings }: { settings: SettingsView }) {
  const save = useSaveFeishuMutation();
  const test = useTestFeishuMutation();
  const [webhookUrl, setWebhookUrl] = useState("");
  const [message, setMessage] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const binding = settings.feishu;
  const busy = save.isPending || test.isPending;

  async function saveAndVerify() {
    setMessage(null);
    try {
      if (webhookUrl.trim()) await save.mutateAsync({ webhookUrl: webhookUrl.trim() });
      const result = await test.mutateAsync();
      setWebhookUrl("");
      setMessage({ tone: "success", text: result.message });
    } catch (error) {
      setMessage({ tone: "error", text: clientErrorMessage(error) });
    }
  }

  async function removeBinding() {
    setMessage(null);
    try {
      await save.mutateAsync({ webhookUrl: "" });
      setWebhookUrl("");
      setMessage({ tone: "success", text: "已解除飞书绑定" });
    } catch (error) {
      setMessage({ tone: "error", text: clientErrorMessage(error) });
    }
  }

  return (
    <div className="connection-binding-form">
      <div className="status-title-line">
        <strong>飞书机器人</strong>
        <StatusBadge
          label={binding.verified ? "已绑定" : binding.configured ? "待验证" : "未绑定"}
          tone={binding.verified ? "success" : binding.configured ? "pending" : "neutral"}
        />
      </div>
      <p>{binding.verified ? "绑定有效，可发送飞书通知" : "绑定自定义机器人 Webhook"}</p>
      <label className="connection-field">
        <span>飞书 Webhook</span>
        <input
          aria-label="飞书 Webhook"
          autoComplete="off"
          disabled={busy}
          onChange={(event) => setWebhookUrl(event.target.value)}
          placeholder={
            binding.configured
              ? "已保存，留空则保留"
              : "https://open.feishu.cn/open-apis/bot/v2/hook/…"
          }
          type="password"
          value={webhookUrl}
        />
      </label>
      <div className="connection-actions">
        <button
          className="button button-primary"
          disabled={busy || (!webhookUrl.trim() && !binding.configured)}
          onClick={() => void saveAndVerify()}
          type="button"
        >
          {busy ? (
            <LoaderCircle aria-hidden="true" className="spin" size={16} />
          ) : (
            <Link2 aria-hidden="true" size={16} />
          )}
          {binding.configured ? "验证绑定" : "保存并绑定"}
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
          href="https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot"
          onClick={(event) => {
            event.preventDefault();
            void window.docmind.shell.openExternal(
              "https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot",
            );
          }}
        >
          添加机器人
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
