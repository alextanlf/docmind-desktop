import { useState } from "react";
import type { RuntimeSettingsInput, SettingsView } from "../../../../shared/contracts";
import { clientErrorMessage, useSaveRuntimeMutation } from "./settings.queries";
import { OllamaStatusCard } from "./OllamaStatusCard";
import { OllamaPullForm } from "./OllamaPullForm";

const fallback: RuntimeSettingsInput = {
  ollama: { baseUrl: "http://127.0.0.1:11434", model: "llama3.2", timeoutSeconds: 60 },
  routing: { mode: "automatic" },
  rag: { maxSources: 5, memoryRecallMinSimilarity: 0.65 },
};

export function RuntimeModelSettings({ settings }: { settings: SettingsView }) {
  const mutation = useSaveRuntimeMutation();
  const initial = settings.runtime ?? fallback;
  const [draft, setDraft] = useState<RuntimeSettingsInput>({
    ...fallback,
    ...initial,
    // A runtime saved before the rag key existed has no `rag`; the schema default
    // only applies on the wire, so fill it in here to keep the inputs controlled.
    rag: initial.rag ?? fallback.rag,
  });
  const update = (patch: Partial<RuntimeSettingsInput>) =>
    setDraft((value) => ({ ...value, ...patch }));
  const save = () => mutation.mutate(draft);
  const modeCopy: Record<RuntimeSettingsInput["routing"]["mode"], string> = {
    local_only: "内容不会发送到云端",
    automatic: "本地不可用时可能使用云端",
    cloud_only: "使用现有云端配置",
  };
  return (
    <div className="runtime-model-settings">
      <fieldset>
        <legend>对话运行方式</legend>
        <div role="radiogroup" aria-label="对话运行方式">
          {(
            [
              ["automatic", "自动回退"],
              ["local_only", "仅本地"],
              ["cloud_only", "仅云端"],
            ] as const
          ).map(([value, label]) => (
            <label key={value}>
              <input
                type="radio"
                role="radio"
                aria-checked={draft.routing.mode === value}
                name="routing-mode"
                value={value}
                checked={draft.routing.mode === value}
                onChange={() => update({ routing: { mode: value } })}
              />
              {label}
            </label>
          ))}
        </div>
        <p role="status">{modeCopy[draft.routing.mode]}</p>
      </fieldset>
      <label>
        Ollama 地址
        <input
          type="url"
          value={draft.ollama.baseUrl}
          onChange={(e) => update({ ollama: { ...draft.ollama, baseUrl: e.target.value } })}
        />
      </label>
      <label>
        本地模型
        <input
          value={draft.ollama.model}
          onChange={(e) => update({ ollama: { ...draft.ollama, model: e.target.value } })}
        />
      </label>
      <fieldset>
        <legend>检索</legend>
        <label>
          送入模型的片段上限（1–20）
          <input
            type="number"
            min={1}
            max={20}
            value={draft.rag.maxSources}
            onChange={(e) =>
              update({ rag: { ...draft.rag, maxSources: Number(e.target.value) } })
            }
          />
        </label>
        <p role="status">
          调高能改善“总结全部”这类全局提问的召回，代价是提示词变长、调用变贵。
        </p>
        <label>
          记忆召回相似度下限（0–1）
          <input
            type="number"
            min={0}
            max={1}
            step={0.05}
            value={draft.rag.memoryRecallMinSimilarity}
            onChange={(e) =>
              update({
                rag: { ...draft.rag, memoryRecallMinSimilarity: Number(e.target.value) },
              })
            }
          />
        </label>
        <p role="status">只影响记忆召回；文档检索刻意不设相似度门槛，以免漏掉短查询。</p>
      </fieldset>
      <button
        className="button button-primary"
        type="button"
        onClick={save}
        disabled={mutation.isPending}
      >
        {mutation.isPending ? "保存中…" : "保存运行设置"}
      </button>
      {mutation.isError && <p role="alert">{clientErrorMessage(mutation.error)}</p>}
      {mutation.isSuccess && <p role="status">运行设置已保存</p>}
      <OllamaStatusCard />
      <OllamaPullForm />
    </div>
  );
}
