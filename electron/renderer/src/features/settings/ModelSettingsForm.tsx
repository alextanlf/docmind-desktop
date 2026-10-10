import { CheckCircle2, LoaderCircle, PlugZap, RefreshCw, Save, Trash2 } from "lucide-react";
import { useRef, useState, type FormEvent } from "react";
import type { AvailableModel, ModelPreset, SettingsView } from "../../../../shared/contracts";
import {
  DEFAULT_CUSTOM_PROVIDER_ID,
  LEGACY_CUSTOM_PRESET,
  NEW_CUSTOM_PROVIDER_ID,
  customPreset,
  isCustomPreset,
} from "../../../../shared/contracts";
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
  // 升级前唯一的那家自定义厂商。它现在只是老配置的字面量与内置表的一部分，
  // **不出现在下拉框里** —— 自定义厂商改成了一张可增删的列表，由「自定义供应商」
  // 那一组渲染（见下面的 optgroup）。`contracts.test.ts` 要求这张表与
  // MODEL_PRESET_IDS 逐字对齐，所以它不能删。
  custom: { label: "自定义", baseUrl: "", model: "" },
} as const satisfies Record<ModelPreset, { label: string; baseUrl: string; model: string }>;

/** 下拉框里「内置服务商」那一组；`custom` 是过去式，交给自定义那一组渲染。 */
const BUILTIN_PRESETS = (Object.keys(PRESETS) as ModelPreset[]).filter(
  (value) => value !== LEGACY_CUSTOM_PRESET,
);

/** 「新建」的占位预设，后端保存时才分配真 id。 */
const NEW_CUSTOM_VALUE = customPreset(NEW_CUSTOM_PROVIDER_ID);
/** 没有档案行的那家「未命名自定义」；升级前裸的 `custom` 也归到它名下。 */
const DEFAULT_CUSTOM_VALUE = customPreset(DEFAULT_CUSTOM_PROVIDER_ID);

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
  // 下拉框的可选项 = 内置厂商 + 用户自建的每一家 + 「新建」。当前预设一定在里面：
  // 后端会把「还没有档案行的自定义配置」也合成一条，所以这里不需要再兜底成
  // 「另一家」—— 那正是表单曾经显示出一个用户从未选择过的厂商的原因。
  const providers = settings.customProviders;
  const providerOptions = new Set([
    ...BUILTIN_PRESETS,
    ...providers.map((provider) => customPreset(provider.id)),
  ]);
  const initialPreset = providerOptions.has(settings.model.preset)
    ? settings.model.preset
    : settings.model.preset === LEGACY_CUSTOM_PRESET
      ? DEFAULT_CUSTOM_VALUE
      : NEW_CUSTOM_VALUE;
  const [preset, setPreset] = useState<string>(initialPreset);
  const [label, setLabel] = useState(
    providers.find((provider) => customPreset(provider.id) === initialPreset)?.label ??
      (isCustomPreset(initialPreset) ? settings.model.label : ""),
  );
  const [baseUrl, setBaseUrl] = useState(settings.model.baseUrl);
  const [model, setModel] = useState(settings.model.model);
  const [reasoningEffort, setReasoningEffort] = useState(settings.model.reasoningEffort);
  const [timeoutSeconds, setTimeoutSeconds] = useState(settings.model.timeoutSeconds);
  const [apiKey, setApiKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [listing, setListing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [liveModels, setLiveModels] = useState<AvailableModel[]>([]);
  const [needsSave, setNeedsSave] = useState(false);
  const [message, setMessage] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const revisionRef = useRef(0);
  const activeOperationRef = useRef<{ kind: "save" | "test"; revision: number } | null>(null);
  const busy = saving || testing;
  // 当前预设对应的自定义档案。`custom:new` 没有档案 —— 它是「还没保存的新建」。
  const currentProvider = providers.find((provider) => customPreset(provider.id) === preset);
  const customPresetSelected = isCustomPreset(preset);
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

  function changePreset(nextPreset: string) {
    // 下拉框的选项由服务端下发的档案列表决定，理论上不会给出别的值；真给了也
    // 不能把表单打挂 —— `PRESETS` 里没有、又不是自定义预设的值直接忽略。
    if (!isCustomPreset(nextPreset) && !(nextPreset in PRESETS)) return;
    setPreset(nextPreset);
    setLiveModels([]);
    setConfirmingDelete(false);
    // 半填的 key 属于上一个预设，不能跟着带到新预设去（那会把 DeepSeek 的 key
    // 存到 Kimi 名下）；清除勾选同理，它针对的是上一个预设的槽位。
    setApiKey("");
    setClearKey(false);
    if (isCustomPreset(nextPreset)) {
      // 每一家自定义供应商带着自己的地址、模型、超时和推理档位 —— 这正是把它们
      // 存成档案（而不是共用一个 `custom` 槽位）换来的东西。没有档案的就是
      // 「新建」，从空表单开始。
      const provider = providers.find((item) => customPreset(item.id) === nextPreset);
      setLabel(provider?.label ?? "");
      setBaseUrl(provider?.baseUrl ?? "");
      setModel(provider?.model ?? "");
      setTimeoutSeconds(provider?.timeoutSeconds ?? 30);
      setReasoningEffort(provider?.reasoningEffort ?? "");
    } else {
      const next = PRESETS[nextPreset as ModelPreset];
      const nextModel = next.model;
      setLabel("");
      setBaseUrl(next.baseUrl);
      setModel(nextModel);
      // A level valid for the old model may be rejected by the new one, so fall
      // back to that model's documented default. Empty means "vendor default".
      setReasoningEffort(
        settings.modelCapabilities[nextPreset]?.[nextModel.toLowerCase()]?.defaultReasoningEffort ??
          "",
      );
    }
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
        // 内置预设不落「显示名」：它们的名字来自上面那张内置表。
        label: isCustomPreset(preset) ? label.trim() : "",
        apiKey: submittedKey,
      });
      appQueryClient.setQueryData(settingsKeys.root, saved);
      if (activeOperationRef.current !== operation || revisionRef.current !== operation.revision)
        return;
      // 刚「新建」的那家：后端分配了真正的档案 id，表单要跟着换到它名下。不然下
      // 一次保存还带着 `custom:new`，会再建一家出来。
      if (saved.model.preset !== preset) setPreset(saved.model.preset);
      if (saved.model.label) setLabel(saved.model.label);
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
      if (!clearKey && saved.apiKeys?.[saved.model.preset]) {
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

  async function removeProvider() {
    const provider = currentProvider;
    if (!provider || activeOperationRef.current || deleting) return;
    setDeleting(true);
    setMessage(null);
    try {
      const saved = await window.docmind.settings.deleteModelProvider(provider.id);
      appQueryClient.setQueryData(settingsKeys.root, saved);
      // 一切以服务端返回的视图为准：删掉的是当前在用的那家时它会回落到「未命名」
      // 槽位，表单不能继续显示一个已经不存在的供应商。
      const fallback = saved.customProviders.find(
        (item) => customPreset(item.id) === saved.model.preset,
      );
      setPreset(saved.model.preset);
      setLabel(fallback?.label ?? saved.model.label);
      setBaseUrl(saved.model.baseUrl);
      setModel(saved.model.model);
      setTimeoutSeconds(saved.model.timeoutSeconds);
      setReasoningEffort(saved.model.reasoningEffort);
      setLiveModels([]);
      setApiKey("");
      setClearKey(false);
      setConfirmingDelete(false);
      // 删除已经落库，界面与后端一致，所以不是「待保存」；只把上一家的连接结论作废。
      setNeedsSave(false);
      revisionRef.current += 1;
      onConnectionInvalidated?.();
      setMessage({ tone: "success", text: `已删除「${provider.label}」` });
    } catch (error) {
      setMessage({ tone: "error", text: clientErrorMessage(error) });
    } finally {
      setDeleting(false);
    }
  }

  return (
    <form className="model-form" onSubmit={save}>
      <div className="form-grid">
        <label>
          <span>模型预设</span>
          <select
            disabled={busy}
            value={preset}
            onChange={(event) => changePreset(event.target.value)}
          >
            <optgroup label="内置服务商">
              {BUILTIN_PRESETS.map((value) => (
                <option key={value} value={value}>
                  {PRESETS[value].label}
                </option>
              ))}
            </optgroup>
            <optgroup label="自定义供应商">
              {providers.map((provider) => (
                <option key={provider.id} value={customPreset(provider.id)}>
                  {provider.label}
                </option>
              ))}
              <option value={NEW_CUSTOM_VALUE}>＋ 新建自定义供应商</option>
            </optgroup>
          </select>
        </label>
        {customPresetSelected ? (
          <label>
            <span>供应商名称</span>
            <input
              disabled={busy}
              maxLength={60}
              onChange={(event) => {
                setLabel(event.target.value);
                invalidateConnection();
              }}
              placeholder="例如：公司网关、某中转站"
              value={label}
            />
          </label>
        ) : null}
        <label className={customPresetSelected ? "form-field-full" : "form-field-wide"}>
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
      {/* 只有落了档案的那一家才可删。升级前裸的 `custom`、还没保存的「新建」都是
          按已保存的配置合成出来的条目，删掉它下次读还会再合成一条 —— 后端也会拒绝，
          所以这里不给出这个按钮。确认做成两步，避免为一个手滑弹一个模态框。 */}
      {currentProvider?.removable ? (
        <div className="provider-delete-row">
          {confirmingDelete ? (
            <>
              <span>将删除「{currentProvider.label}」及其已保存的 API Key，此操作无法撤销。</span>
              <button
                className="button button-danger"
                disabled={busy || deleting}
                onClick={() => void removeProvider()}
                type="button"
              >
                {deleting ? (
                  <LoaderCircle aria-hidden="true" className="spin" size={16} />
                ) : (
                  <Trash2 aria-hidden="true" size={16} />
                )}
                {deleting ? "删除中…" : "确认删除"}
              </button>
              <button
                className="button button-secondary"
                disabled={deleting}
                onClick={() => setConfirmingDelete(false)}
                type="button"
              >
                取消
              </button>
            </>
          ) : (
            <button
              className="button button-danger-quiet"
              disabled={busy}
              onClick={() => setConfirmingDelete(true)}
              type="button"
            >
              <Trash2 aria-hidden="true" size={16} />
              删除该供应商
            </button>
          )}
        </div>
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
