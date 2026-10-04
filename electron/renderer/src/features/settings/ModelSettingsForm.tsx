import { CheckCircle2, LoaderCircle, PlugZap, RefreshCw, Save } from "lucide-react";
import { useRef, useState, type FormEvent } from "react";
import type { AvailableModel, SettingsView } from "../../../../shared/contracts";
import { appQueryClient } from "../../app/query-client";
import { clientErrorMessage, settingsKeys } from "./settings.queries";

const PRESETS = {
  deepseek: { label: "DeepSeek", baseUrl: "https://api.deepseek.com/v1", model: "deepseek-chat" },
  qwen: {
    label: "通义千问",
    baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    model: "qwen-plus",
  },
  kimi: { label: "Kimi (月之暗面)", baseUrl: "https://api.moonshot.cn/v1", model: "kimi-k2.5" },
  glm: { label: "智谱 GLM", baseUrl: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4.6" },
  mimo: { label: "小米 MiMo", baseUrl: "https://api.xiaomimimo.com/v1", model: "mimo-v2.5-pro" },
  opencode_zen: {
    label: "OpenCode Zen（按量·含免费档）",
    baseUrl: "https://opencode.ai/zen/v1",
    model: "mimo-v2.5-free",
  },
  opencode_go: {
    label: "OpenCode Go（$10/月订阅）",
    baseUrl: "https://opencode.ai/zen/go/v1",
    model: "mimo-v2.5",
  },
  openai: { label: "OpenAI", baseUrl: "https://api.openai.com/v1", model: "gpt-5-mini" },
  custom: { label: "自定义", baseUrl: "", model: "" },
} as const;

// Unified levels rendered as the same four meanings across vendors; the backend
// translates each to the vendor's own field or drops it when unsupported.
const EFFORT_LABELS: Record<string, string> = {
  off: "关闭思考",
  low: "轻量",
  medium: "标准",
  high: "深度",
};

type Preset = keyof typeof PRESETS;

const CUSTOM_MODEL_VALUE = "__custom__";

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
  const [reasoningEffort, setReasoningEffort] = useState(settings.model.reasoningEffort);
  const [timeoutSeconds, setTimeoutSeconds] = useState(settings.model.timeoutSeconds);
  const [apiKey, setApiKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [hasSavedKey, setHasSavedKey] = useState(settings.hasApiKey);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [listing, setListing] = useState(false);
  const [liveModels, setLiveModels] = useState<AvailableModel[]>([]);
  const [needsSave, setNeedsSave] = useState(false);
  const [message, setMessage] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const revisionRef = useRef(0);
  const activeOperationRef = useRef<{ kind: "save" | "test"; revision: number } | null>(null);
  const busy = saving || testing;
  const savedKeyPlaceholder = "••••••••";

  // A picker is only useful when the provider actually offers choices. The
  // curated catalogue (or a fetched live list) supplies them; with neither, the
  // field stays free text so any model id remains typeable. The current value
  // is shown alongside those choices but never becomes the sole option, which
  // would turn an empty catalogue into a one-item dropdown.
  const curatedModels = settings.modelPresets[preset] ?? [];
  const knownModels = (() => {
    const merged = [...curatedModels];
    const seen = new Set(merged.map((item) => item.id));
    for (const item of liveModels) {
      if (!seen.has(item.id)) {
        seen.add(item.id);
        merged.push(item);
      }
    }
    return merged;
  })();
  const hasCatalogue = knownModels.length > 0;
  // Keep an id the user already chose selectable even if the provider does not
  // report it, otherwise saving would silently rewrite the field.
  const isCustomModel = !knownModels.some((item) => item.id === model);
  // Only presets the backend reports as adjustable get a level picker; the rest
  // keep the vendor's own default, since sending a field it does not accept
  // would fail the request.
  const effortLevels = settings.modelCapabilities[preset]?.reasoningLevels ?? [];
  const options =
    hasCatalogue && !isCustomModel
      ? knownModels
      : hasCatalogue && model
        ? [...knownModels, { id: model, label: model }]
        : knownModels;

  function invalidateConnection() {
    revisionRef.current += 1;
    setNeedsSave(true);
    setMessage(null);
    onConnectionInvalidated?.();
  }

  function changePreset(nextPreset: Preset) {
    const next = PRESETS[nextPreset];
    setPreset(nextPreset);
    setBaseUrl(next.baseUrl);
    setModel(next.model);
    // A level valid for the old vendor may be rejected by the new one, so fall
    // back to that preset's documented default. Empty means "vendor default".
    setReasoningEffort(settings.modelCapabilities[nextPreset]?.defaultReasoningEffort ?? "");
    setLiveModels([]);
    invalidateConnection();
  }

  async function refreshModels() {
    if (listing) return;
    setListing(true);
    setMessage(null);
    const submittedKey = clearKey ? "" : apiKey.trim() || undefined;
    try {
      const result = await window.docmind.settings.listModels({
        baseUrl: baseUrl.trim() || undefined,
        model: model.trim() || undefined,
        apiKey: submittedKey,
      });
      setLiveModels(result.models);
      if (result.models.length > 0) {
        setMessage({ tone: "success", text: `已获取 ${result.models.length} 个可用模型` });
      } else {
        setMessage({ tone: "error", text: "该服务商未返回模型列表，请手动填写模型名称" });
      }
    } catch (error) {
      setMessage({ tone: "error", text: clientErrorMessage(error) });
    } finally {
      setListing(false);
    }
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (activeOperationRef.current) return;
    const operation = { kind: "save" as const, revision: revisionRef.current };
    activeOperationRef.current = operation;
    onConnectionInvalidated?.();
    setSaving(true);
    setMessage(null);
    try {
      const submittedKey = clearKey ? "" : apiKey.trim() || undefined;
      const saved = await window.docmind.settings.saveModel({
        preset,
        baseUrl: baseUrl.trim(),
        model: model.trim(),
        timeoutSeconds,
        reasoningEffort,
        apiKey: submittedKey,
      });
      appQueryClient.setQueryData(settingsKeys.root, saved);
      if (activeOperationRef.current !== operation || revisionRef.current !== operation.revision)
        return;
      setHasSavedKey(saved.hasApiKey);
      setApiKey("");
      setClearKey(false);
      setNeedsSave(false);
      setMessage({ tone: "success", text: "设置已保存" });
      activeOperationRef.current = null;
      setSaving(false);
      if (saved.hasApiKey && !clearKey) {
        await runConnectionTest();
      }
    } catch (error) {
      if (activeOperationRef.current !== operation || revisionRef.current !== operation.revision)
        return;
      setMessage({ tone: "error", text: clientErrorMessage(error) });
    } finally {
      if (activeOperationRef.current === operation) {
        activeOperationRef.current = null;
        setSaving(false);
      }
    }
  }

  async function runConnectionTest() {
    const operation = { kind: "test" as const, revision: revisionRef.current };
    activeOperationRef.current = operation;
    setTesting(true);
    setMessage(null);
    try {
      const result = await window.docmind.settings.testModel();
      if (activeOperationRef.current !== operation || revisionRef.current !== operation.revision)
        return;
      if (!result.connected) {
        setMessage({ tone: "error", text: "模型未能建立连接，请检查设置" });
        return;
      }
      setMessage({ tone: "success", text: `连接成功，延迟 ${result.latencyMs} 毫秒` });
      onConnectionSuccess?.();
    } catch (error) {
      if (activeOperationRef.current !== operation || revisionRef.current !== operation.revision)
        return;
      setMessage({ tone: "error", text: clientErrorMessage(error) });
    } finally {
      if (activeOperationRef.current === operation) {
        activeOperationRef.current = null;
        setTesting(false);
      }
    }
  }

  async function testConnection() {
    if (activeOperationRef.current || needsSave || !hasSavedKey) return;
    await runConnectionTest();
  }

  return (
    <form className="model-form" onSubmit={save}>
      <div className="form-grid">
        <label>
          <span>模型预设</span>
          <select
            disabled={busy}
            value={preset}
            onChange={(event) => changePreset(event.target.value as Preset)}
          >
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
            disabled={busy}
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
          {hasCatalogue && !isCustomModel ? (
            <select
              disabled={busy}
              value={model}
              onChange={(event) => {
                const next = event.target.value;
                setModel(next === CUSTOM_MODEL_VALUE ? "" : next);
                invalidateConnection();
              }}
            >
              {options.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label === option.id ? option.id : `${option.label}（${option.id}）`}
                </option>
              ))}
              <option value={CUSTOM_MODEL_VALUE}>自定义…</option>
            </select>
          ) : (
            <input
              disabled={busy}
              value={model}
              onChange={(event) => {
                setModel(event.target.value);
                invalidateConnection();
              }}
              placeholder="输入模型名称"
            />
          )}
          {hasCatalogue && isCustomModel ? (
            <button
              className="button button-secondary"
              disabled={busy}
              onClick={() => {
                const first = options[0];
                if (first) {
                  setModel(first.id);
                  invalidateConnection();
                }
              }}
              type="button"
            >
              从列表中选择
            </button>
          ) : null}
        </label>
        <label>
          <span>推理强度</span>
          {effortLevels.length > 0 ? (
            <select
              disabled={busy}
              value={reasoningEffort || effortLevels[0]}
              onChange={(event) => {
                setReasoningEffort(event.target.value);
                invalidateConnection();
              }}
            >
              {effortLevels.map((level) => (
                <option key={level} value={level}>
                  {EFFORT_LABELS[level] ?? level}
                </option>
              ))}
            </select>
          ) : (
            <p className="field-hint">该服务商未提供推理档位，使用其默认行为</p>
          )}
        </label>
        <label>
          <span>超时时间（秒）</span>
          <input
            disabled={busy}
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
            disabled={clearKey || busy}
            onChange={(event) => {
              setApiKey(event.target.value);
              invalidateConnection();
            }}
            placeholder={hasSavedKey ? "已安全保存，留空可保留" : "请输入 API Key"}
            type="password"
            value={hasSavedKey && !clearKey && apiKey === "" ? savedKeyPlaceholder : apiKey}
          />
        </label>
      </div>
      {hasSavedKey ? (
        <label className="checkbox-row">
          <input
            checked={clearKey}
            disabled={busy}
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
        <button
          className="button button-secondary"
          disabled={busy || listing}
          onClick={() => void refreshModels()}
          type="button"
        >
          {listing ? (
            <LoaderCircle aria-hidden="true" className="spin" size={16} />
          ) : (
            <RefreshCw aria-hidden="true" size={16} />
          )}
          {listing ? "获取中" : "获取模型列表"}
        </button>
        <button className="button button-secondary" disabled={busy} type="submit">
          {saving ? (
            <LoaderCircle aria-hidden="true" className="spin" size={16} />
          ) : (
            <Save aria-hidden="true" size={16} />
          )}
          保存设置
        </button>
        <button
          className="button button-primary"
          disabled={!hasSavedKey || needsSave || busy}
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
