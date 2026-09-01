import { CheckCircle2, LoaderCircle, PlugZap, Save } from "lucide-react";
import { useState, type FormEvent } from "react";
import type { SettingsView } from "../../../../shared/contracts";
import { appQueryClient } from "../../app/query-client";
import { clientErrorMessage, settingsKeys } from "./settings.queries";

const PRESETS = {
  deepseek: { label: "DeepSeek", baseUrl: "https://api.deepseek.com/v1", model: "deepseek-chat" },
  qwen: {
    label: "通义千问",
    baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    model: "qwen-plus",
  },
  openai: { label: "OpenAI", baseUrl: "https://api.openai.com/v1", model: "gpt-5-mini" },
  custom: { label: "自定义", baseUrl: "", model: "" },
} as const;

type Preset = keyof typeof PRESETS;

type Props = {
  settings: SettingsView;
  testButtonLabel?: string;
  onConnectionSuccess?: () => void;
  onConnectionInvalidated?: () => void;
};

export function ModelSettingsForm({
  settings,
  testButtonLabel = "测试连接",
  onConnectionSuccess,
  onConnectionInvalidated,
}: Props) {
  const initialPreset =
    settings.model.preset in PRESETS ? (settings.model.preset as Preset) : "custom";
  const [preset, setPreset] = useState<Preset>(initialPreset);
  const [baseUrl, setBaseUrl] = useState(settings.model.baseUrl);
  const [model, setModel] = useState(settings.model.model);
  const [timeoutSeconds, setTimeoutSeconds] = useState(settings.model.timeoutSeconds);
  const [apiKey, setApiKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [hasSavedKey, setHasSavedKey] = useState(settings.hasApiKey);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [needsSave, setNeedsSave] = useState(false);
  const [message, setMessage] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  function invalidateConnection() {
    setNeedsSave(true);
    setMessage(null);
    onConnectionInvalidated?.();
  }

  function changePreset(nextPreset: Preset) {
    const next = PRESETS[nextPreset];
    setPreset(nextPreset);
    setBaseUrl(next.baseUrl);
    setModel(next.model);
    invalidateConnection();
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    onConnectionInvalidated?.();
    setSaving(true);
    setMessage(null);
    try {
      const saved = await window.docmind.settings.saveModel({
        preset,
        baseUrl: baseUrl.trim(),
        model: model.trim(),
        timeoutSeconds,
        apiKey: clearKey ? "" : apiKey.trim() || undefined,
      });
      appQueryClient.setQueryData(settingsKeys.root, saved);
      setHasSavedKey(saved.hasApiKey);
      setApiKey("");
      setClearKey(false);
      setNeedsSave(false);
      setMessage({ tone: "success", text: "设置已保存" });
    } catch (error) {
      setMessage({ tone: "error", text: clientErrorMessage(error) });
    } finally {
      setSaving(false);
    }
  }

  async function testConnection() {
    setTesting(true);
    setMessage(null);
    try {
      const result = await window.docmind.settings.testModel();
      if (!result.connected) {
        setMessage({ tone: "error", text: "模型未能建立连接，请检查设置" });
        return;
      }
      setMessage({ tone: "success", text: `连接成功，延迟 ${result.latencyMs} 毫秒` });
      onConnectionSuccess?.();
    } catch (error) {
      setMessage({ tone: "error", text: clientErrorMessage(error) });
    } finally {
      setTesting(false);
    }
  }

  return (
    <form className="model-form" onSubmit={save}>
      <div className="form-grid">
        <label>
          <span>模型预设</span>
          <select value={preset} onChange={(event) => changePreset(event.target.value as Preset)}>
            {Object.entries(PRESETS).map(([value, option]) => (
              <option key={value} value={value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <label className="form-field-wide">
          <span>Base URL</span>
          <input
            value={baseUrl}
            onChange={(event) => {
              setBaseUrl(event.target.value);
              invalidateConnection();
            }}
            type="url"
          />
        </label>
        <label>
          <span>模型名称</span>
          <input
            value={model}
            onChange={(event) => {
              setModel(event.target.value);
              invalidateConnection();
            }}
          />
        </label>
        <label>
          <span>超时时间（秒）</span>
          <input
            max={300}
            min={1}
            onChange={(event) => {
              setTimeoutSeconds(Number(event.target.value));
              invalidateConnection();
            }}
            type="number"
            value={timeoutSeconds}
          />
        </label>
        <label className="form-field-wide">
          <span>API Key</span>
          <input
            autoComplete="off"
            disabled={clearKey}
            onChange={(event) => {
              setApiKey(event.target.value);
              invalidateConnection();
            }}
            placeholder={hasSavedKey ? "已安全保存，留空可保留" : "请输入 API Key"}
            type="password"
            value={apiKey}
          />
        </label>
      </div>
      {hasSavedKey ? (
        <label className="checkbox-row">
          <input
            checked={clearKey}
            onChange={(event) => {
              setClearKey(event.target.checked);
              invalidateConnection();
            }}
            type="checkbox"
          />
          <span>清除已保存的 API Key</span>
        </label>
      ) : null}
      {message ? (
        <p
          className={`form-message form-message-${message.tone}`}
          role={message.tone === "error" ? "alert" : "status"}
        >
          {message.tone === "success" ? <CheckCircle2 aria-hidden="true" size={16} /> : null}
          {message.text}
        </p>
      ) : null}
      <div className="form-actions">
        <button className="button button-secondary" disabled={saving} type="submit">
          {saving ? (
            <LoaderCircle aria-hidden="true" className="spin" size={16} />
          ) : (
            <Save aria-hidden="true" size={16} />
          )}
          保存设置
        </button>
        <button
          className="button button-primary"
          disabled={!hasSavedKey || needsSave || testing || saving}
          onClick={testConnection}
          type="button"
        >
          {testing ? (
            <LoaderCircle aria-hidden="true" className="spin" size={16} />
          ) : (
            <PlugZap aria-hidden="true" size={16} />
          )}
          {testButtonLabel}
        </button>
      </div>
    </form>
  );
}
