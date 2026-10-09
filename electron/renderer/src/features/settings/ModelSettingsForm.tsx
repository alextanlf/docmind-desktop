import { CheckCircle2, LoaderCircle, PlugZap, RefreshCw, Save } from "lucide-react";
import { useRef, useState, type FormEvent } from "react";
import type { AvailableModel, ModelPreset, SettingsView } from "../../../../shared/contracts";
import { appQueryClient } from "../../app/query-client";
import { clientErrorMessage, settingsKeys } from "./settings.queries";

const PRESETS = {
  deepseek: { label: "DeepSeek", baseUrl: "https://api.deepseek.com/v1", model: "deepseek-flash" },
  qwen: {
    label: "通义千问",
    baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    model: "qwen3.8-max",
  },
  kimi: { label: "Kimi (月之暗面)", baseUrl: "https://api.moonshot.cn/v1", model: "kimi-k3" },
  glm: { label: "智谱 GLM", baseUrl: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4.6" },
  mimo: { label: "小米 MiMo", baseUrl: "https://api.xiaomimimo.com/v1", model: "mimo-v2.6-pro" },
  // Zen is restricted to its free tier by the backend; the label must not imply
  // that paid ids are selectable here.
  opencode_zen: {
    label: "OpenCode Zen（仅免费模型）",
    baseUrl: "https://opencode.ai/zen/v1",
    model: "mimo-v2.6-flash-free",
  },
  opencode_go: {
    label: "OpenCode Go（$10/月订阅）",
    baseUrl: "https://opencode.ai/zen/go/v1",
    model: "mimo-v2.6-pro",
  },
  openai: { label: "OpenAI", baseUrl: "https://api.openai.com/v1", model: "gpt-5.6-terra" },
  custom: { label: "自定义", baseUrl: "", model: "" },
} as const satisfies Record<ModelPreset, { label: string; baseUrl: string; model: string }>;

// 档位标签直接对应厂商原生值。后端下发的 levels 就是官方文档里的档位
// （Kimi K3 = low/high/max，GLM-4.6 = 开/关两态，GPT-5.6 = none…xhigh），
// 不再把它们压成自造的「轻量/标准/深度」—— 那个抽象层会丢掉 max/xhigh/none，
// 而且「深度」在不同厂商之间根本不是同一件事。
const EFFORT_LABELS: Record<string, string> = {
  off: "关闭思考",
  on: "开启思考",
  none: "不推理",
  minimal: "最低",
  low: "低",
  medium: "中",
  high: "高",
  xhigh: "很高",
  max: "最高",
};

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
    settings.model.preset in PRESETS ? (settings.model.preset as ModelPreset) : "custom";
  const [preset, setPreset] = useState<ModelPreset>(initialPreset);
  const [baseUrl, setBaseUrl] = useState(settings.model.baseUrl);
  const [model, setModel] = useState(settings.model.model);
  const [reasoningEffort, setReasoningEffort] = useState(settings.model.reasoningEffort);
  const [timeoutSeconds, setTimeoutSeconds] = useState(settings.model.timeoutSeconds);
  const [apiKey, setApiKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [listing, setListing] = useState(false);
  const [liveModels, setLiveModels] = useState<AvailableModel[]>([]);
  const [needsSave, setNeedsSave] = useState(false);
  const [message, setMessage] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const revisionRef = useRef(0);
  const activeOperationRef = useRef<{ kind: "save" | "test"; revision: number } | null>(null);
  const busy = saving || testing;
  // 🔴 key 是按预设存的，不是全局的：一把 DeepSeek 的 key 打不通 Kimi。这里必须
  // 按当前预设查，不能拿 settings.hasApiKey（它只描述「已保存的那一家」）冒充 ——
  // 那正是「切到别家仍显示已安全保存」的来源。
  //
  // `clearKey` 只影响这把 key 还算不算数，槽位本身还在，所以「清除已保存的 API
  // Key」勾选框的可见性看槽位，否则勾上之后按钮自己就消失了。
  const storedKeyForPreset = settings.apiKeys?.[preset] ?? false;
  const hasSavedKey = storedKeyForPreset && !clearKey;
  // 🔴 不要把掩码当受控 value 回填输入框。此前 `value={hasSavedKey ? "••••••••" : apiKey}`
  // 让用户聚焦后输入的每个字符都变成 "••••••••x"，onChange 拿到含掩码的串，
  // 提交时 `apiKey: "••••••••x"` 被写进钥匙串 —— 真实密钥被覆盖且无法恢复。
  // 「已保存」的提示走 placeholder，value 恒为用户真实输入。

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
  // Capabilities are per model, not per vendor: Kimi K3 and K2.6 use different
  // fields, and some models cannot turn thinking off at all.
  const effortLevels =
    settings.modelCapabilities[preset]?.[model.trim().toLowerCase()]?.reasoningLevels ?? [];
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

  function changePreset(nextPreset: ModelPreset) {
    const next = PRESETS[nextPreset];
    const nextModel = next.model;
    setPreset(nextPreset);
    setBaseUrl(next.baseUrl);
    setModel(nextModel);
    // A level valid for the old model may be rejected by the new one, so fall
    // back to that model's documented default. Empty means "vendor default".
    setReasoningEffort(
      settings.modelCapabilities[nextPreset]?.[nextModel.toLowerCase()]?.defaultReasoningEffort ?? "",
    );
    setLiveModels([]);
    // 半填的 key 属于上一个预设，不能跟着带到新预设去（那会把 DeepSeek 的 key
    // 存到 Kimi 名下）；清除勾选同理，它针对的是上一个预设的槽位。
    setApiKey("");
    setClearKey(false);
    invalidateConnection();
  }

  function changeModel(nextModel: string) {
    setModel(nextModel);
    // Same reasoning as switching preset: reset to the new model's default so a
    // level the vendor does not accept is never sent.
    setReasoningEffort(
      settings.modelCapabilities[preset]?.[nextModel.trim().toLowerCase()]?.defaultReasoningEffort ?? "",
    );
    invalidateConnection();
  }

  async function refreshModels() {
    if (listing) return;
    setListing(true);
    setMessage(null);
    const submittedKey = clearKey ? "" : apiKey.trim() || undefined;
    try {
      const result = await window.docmind.settings.listModels({
        preset,
        baseUrl: baseUrl.trim() || undefined,
        model: model.trim() || undefined,
        apiKey: submittedKey,
      });
      setLiveModels(result.models);
      if (result.models.length === 0) {
        setMessage({
          tone: "error",
          text: result.notice ?? "该服务商未返回模型列表，请手动填写模型名称",
        });
      } else if (result.source === "curated") {
        // Never say "已获取 N 个模型" for a hardcoded list — the user needs to
        // know the provider was never reached, and why.
        setMessage({
          tone: result.notice ? "error" : "success",
          text: result.notice ?? `已加载 ${result.models.length} 个内置模型（未配置 API Key）`,
        });
      } else {
        setMessage({ tone: "success", text: `已获取 ${result.models.length} 个可用模型` });
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
      // No local "hasSavedKey" to sync: it is derived from the refetched view's
      // apiKeys map, which the line above already replaced.
      setApiKey("");
      setClearKey(false);
      setNeedsSave(false);
      setMessage({ tone: "success", text: "设置已保存" });
      activeOperationRef.current = null;
      setSaving(false);
      // Only test when this preset actually has a key now; otherwise the backend
      // would answer "请先配置 API Key" for a save that was meant to be keyless.
      if (!clearKey && saved.apiKeys?.[preset]) {
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
            onChange={(event) => changePreset(event.target.value as ModelPreset)}
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
                if (next === CUSTOM_MODEL_VALUE) {
                  setModel("");
                  invalidateConnection();
                } else {
                  changeModel(next);
                }
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
                if (first) changeModel(first.id);
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
                  {/* Show the raw vendor value next to the gloss so the choice
                      can be matched against that vendor's own docs. */}
                  {EFFORT_LABELS[level] ? `${EFFORT_LABELS[level]}（${level}）` : level}
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
            value={apiKey}
          />
        </label>
      </div>
      {storedKeyForPreset ? (
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
