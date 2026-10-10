import { CheckCircle2, LoaderCircle, PlugZap, RefreshCw, Save } from "lucide-react";
import { useRef, useState, type FormEvent } from "react";
import type {
  AvailableModel,
  ConfiguredModel,
  ModelPreset,
  SettingsView,
} from "../../../../shared/contracts";
import { NEW_CUSTOM_PROVIDER_ID, customPreset, isCustomPreset } from "../../../../shared/contracts";
import { appQueryClient } from "../../app/query-client";
import { BUILTIN_PRESETS, PRESETS, presetDefaults } from "./model-presets";
import { clientErrorMessage, settingsKeys } from "./settings.queries";

/** 「新建」的占位预设，后端保存时才分配真 id。 */
const NEW_CUSTOM_VALUE = customPreset(NEW_CUSTOM_PROVIDER_ID);

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

/** 默认新增哪一家：内置表的第一家，它有内置地址、默认模型和模型目录。 */
const DEFAULT_NEW_PRESET: string = BUILTIN_PRESETS[0] ?? "deepseek";

type Props = {
  settings: SettingsView;
  /** 正在编辑的那一条配置；新增时为 `null`。 */
  profile?: ConfiguredModel | null;
  testButtonLabel?: string;
  /** 保存成功。参数是后端返回的整份视图 —— 弹窗据此关闭，列表据此刷新。 */
  onSaved?: (settings: SettingsView) => void;
  onConnectionSuccess?: () => void;
  onConnectionInvalidated?: () => void;
  /** 有的话渲染「取消」；引导页内联使用时没有可取消的去处。 */
  onCancel?: () => void;
};

/**
 * 一条模型配置的编辑表单 —— 添加与编辑共用它。
 *
 * 🔴 它**不再编辑「当前那一个模型」**，而是编辑列表里被点开的那一条：内置厂商
 * 因此可以存好几条（同一把 key、不同模型），自定义厂商的连接参数也只属于它自己。
 * 「测试连接」与「获取模型列表」都把这份还没保存的草稿发给后端，所以按钮问的是
 * 眼前这一条，而不是当前在用的那条 —— 后者会给出一个与表单无关的结论。
 */
export function ModelSettingsForm({
  settings,
  profile = null,
  testButtonLabel = "测试连接",
  onSaved,
  onConnectionSuccess,
  onConnectionInvalidated,
  onCancel,
}: Props) {
  const initialPreset = profile?.preset ?? DEFAULT_NEW_PRESET;
  const initialModel = profile?.model ?? presetDefaults(initialPreset).model;
  const [preset, setPreset] = useState<string>(initialPreset);
  const [label, setLabel] = useState(profile?.label ?? "");
  const [baseUrl, setBaseUrl] = useState(profile?.baseUrl ?? presetDefaults(initialPreset).baseUrl);
  const [model, setModel] = useState(initialModel);
  const [reasoningEffort, setReasoningEffort] = useState(
    profile?.reasoningEffort ||
      settings.modelCapabilities[initialPreset]?.[initialModel.trim().toLowerCase()]
        ?.defaultReasoningEffort ||
      "",
  );
  const [timeoutSeconds, setTimeoutSeconds] = useState(profile?.timeoutSeconds ?? 30);
  const [apiKey, setApiKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [listing, setListing] = useState(false);
  const [liveModels, setLiveModels] = useState<AvailableModel[]>([]);
  const [message, setMessage] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const revisionRef = useRef(0);
  const activeOperationRef = useRef<{ kind: "save" | "test"; revision: number } | null>(null);
  const busy = saving || testing;
  const customPresetSelected = isCustomPreset(preset);
  // 这个供应商的下拉框选项 = 用户已经配过的每一家自定义供应商。同名同 id 的多条
  // 配置共用一个选项 —— 选了它就是把这一家的连接参数填进来。
  const customConnections = customConnectionsOf(settings.models);
  // 🔴 key 是按供应商存的，不是全局的：一把 DeepSeek 的 key 打不通 Kimi。这里必须
  // 按当前选中的供应商查，不能拿 settings.hasApiKey（它只描述当前在用的那条）冒充。
  //
  // `clearKey` 只影响这把 key 还算不算数，槽位本身还在，所以「清除已保存的 API Key」
  // 勾选框的可见性看槽位，否则勾上之后按钮自己就消失了。
  const storedKeyForPreset = settings.apiKeys?.[preset] ?? false;
  // 🔴 不要把掩码当受控 value 回填输入框。此前 `value={hasSavedKey ? "••••••••" : apiKey}`
  // 让用户聚焦后输入的每个字符都变成 "••••••••x"，onChange 拿到含掩码的串，
  // 提交时 `apiKey: "••••••••x"` 被写进钥匙串 —— 真实密钥被覆盖且无法恢复。
  // 「已保存」的提示走 placeholder，value 恒为用户真实输入。
  const typedKey = apiKey.trim();
  const keyAvailable = typedKey !== "" || (storedKeyForPreset && !clearKey);

  // A picker is only useful when the provider actually offers choices. The
  // curated catalogue (or a fetched live list) supplies them; with neither, the
  // field stays free text so any model id remains typeable.
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
    setMessage(null);
    onConnectionInvalidated?.();
  }

  function changePreset(nextPreset: string) {
    // 下拉框的选项由服务端下发的档案列表决定，理论上不会给出别的值；真给了也
    // 不能把表单打挂 —— 内置表里没有、又不是自定义预设的值直接忽略。
    if (!isCustomPreset(nextPreset) && !(nextPreset in PRESETS)) return;
    setPreset(nextPreset);
    setLiveModels([]);
    // 半填的 key 属于上一个供应商，不能跟着带到新的一家去（那会把 DeepSeek 的 key
    // 存到 Kimi 名下）；清除勾选同理，它针对的是上一家的槽位。
    setApiKey("");
    setClearKey(false);
    if (isCustomPreset(nextPreset)) {
      // 每一家自定义供应商带着自己的地址、模型、超时和推理档位。选「新建」就是
      // 从空表单开始，后端保存时才分配真 id。
      const existing = settings.models.find((item) => item.preset === nextPreset) ?? null;
      setLabel(existing?.label ?? "");
      setBaseUrl(existing?.baseUrl ?? "");
      setModel(existing?.model ?? "");
      setTimeoutSeconds(existing?.timeoutSeconds ?? 30);
      setReasoningEffort(existing?.reasoningEffort ?? "");
    } else {
      const preset = PRESETS[nextPreset as ModelPreset];
      setLabel("");
      setBaseUrl(preset.baseUrl);
      setModel(preset.model);
      // A level valid for the old model may be rejected by the new one, so fall
      // back to that model's documented default. Empty means "vendor default".
      setReasoningEffort(
        settings.modelCapabilities[nextPreset]?.[preset.model.toLowerCase()]
          ?.defaultReasoningEffort ?? "",
      );
    }
    invalidateConnection();
  }

  function changeModel(nextModel: string) {
    setModel(nextModel);
    // Same reasoning as switching preset: reset to the new model's default so a
    // level the vendor does not accept is never sent.
    setReasoningEffort(
      settings.modelCapabilities[preset]?.[nextModel.trim().toLowerCase()]
        ?.defaultReasoningEffort ?? "",
    );
    invalidateConnection();
  }

  /** 这一份草稿。测试连接与获取模型列表都带着它走，所以答案描述的是眼前的表单。 */
  function draftProbe() {
    return {
      preset,
      baseUrl: baseUrl.trim() || undefined,
      model: model.trim() || undefined,
      // 显式清空就是「这把 key 不算数」，不能被后端回退成槽位里那把旧的。
      apiKey: clearKey ? "" : typedKey || undefined,
    };
  }

  async function refreshModels() {
    if (listing) return;
    setListing(true);
    setMessage(null);
    try {
      const result = await window.docmind.settings.listModels(draftProbe());
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
    // 保存**不**作废刚验证过的连接结论：存下去的就是刚才测的那一份。真改过内容的话
    // 那次编辑已经作废了它，这里再作废一次只会逼用户为同一份配置测两遍。
    setSaving(true);
    setMessage(null);
    try {
      const saved = await window.docmind.settings.saveModel({
        // 带 id 就是改这一条；不带就是新增，后端先按「供应商 + 模型」认一条已有的。
        profileId: profile?.id,
        preset,
        baseUrl: baseUrl.trim(),
        model: model.trim(),
        timeoutSeconds,
        reasoningEffort,
        // 内置预设不落「显示名」：它们的名字来自内置表。
        label: isCustomPreset(preset) ? label.trim() : "",
        apiKey: clearKey ? "" : typedKey || undefined,
      });
      appQueryClient.setQueryData(settingsKeys.root, saved);
      if (activeOperationRef.current !== operation || revisionRef.current !== operation.revision)
        return;
      setApiKey("");
      setClearKey(false);
      setMessage({ tone: "success", text: "设置已保存" });
      activeOperationRef.current = null;
      setSaving(false);
      // 保存后的视图由调用方接管：弹窗要关，列表要换，引导页要往下走。
      onSaved?.(saved);
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
      const result = await window.docmind.settings.testModel(draftProbe());
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
    if (activeOperationRef.current || !model.trim() || !keyAvailable) return;
    await runConnectionTest();
  }

  return (
    <form className="model-form" onSubmit={save}>
      <div className="form-grid">
        <label>
          <span>供应商</span>
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
              {customConnections.map((provider) => (
                <option key={provider.preset} value={provider.preset}>
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
            placeholder={storedKeyForPreset ? "已安全保存，留空可保留" : "请输入 API Key"}
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
          disabled={!keyAvailable || !model.trim() || busy}
          onClick={() => void testConnection()}
          type="button"
        >
          {testing ? (
            <LoaderCircle aria-hidden="true" className="spin" size={16} />
          ) : (
            <PlugZap aria-hidden="true" size={16} />
          )}
          {testButtonLabel}
        </button>
        {onCancel ? (
          <button
            className="button button-secondary"
            disabled={busy}
            onClick={onCancel}
            type="button"
          >
            取消
          </button>
        ) : null}
      </div>
    </form>
  );
}

/** 下拉框里那一组「已有的自定义供应商」：同 id 的多条配置只出一个选项。 */
function customConnectionsOf(models: ConfiguredModel[]): ConfiguredModel[] {
  const seen = new Set<string>();
  const connections: ConfiguredModel[] = [];
  for (const item of models) {
    if (!isCustomPreset(item.preset) || seen.has(item.preset)) continue;
    seen.add(item.preset);
    connections.push(item);
  }
  return connections;
}
