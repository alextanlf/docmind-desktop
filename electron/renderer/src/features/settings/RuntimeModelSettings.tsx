import { useState } from "react";
import type { RuntimeSettingsInput, SettingsView } from "../../../../shared/contracts";
import { clientErrorMessage, useSaveRuntimeMutation } from "./settings.queries";
import { OllamaStatusCard } from "./OllamaStatusCard";
import { OllamaPullForm } from "./OllamaPullForm";

const fallback: RuntimeSettingsInput = { ollama: { baseUrl: "http://127.0.0.1:11434", model: "llama3.2", timeoutSeconds: 60 }, routing: { mode: "automatic" } };

export function RuntimeModelSettings({ settings }: { settings: SettingsView }) {
  const mutation = useSaveRuntimeMutation();
  const initial = settings.runtime ?? fallback;
  const [draft, setDraft] = useState<RuntimeSettingsInput>(initial);
  const update = (patch: Partial<RuntimeSettingsInput>) => setDraft((value) => ({ ...value, ...patch }));
  const save = () => mutation.mutate(draft);
  return <div className="runtime-model-settings">
    <fieldset>
      <legend>运行模式</legend>
      <div role="radiogroup" aria-label="运行模式">
        {([['automatic','自动回退'],['local_only','仅本地'],['cloud_only','仅云端']] as const).map(([value,label]) => <label key={value}>
          <input type="radio" name="routing-mode" value={value} checked={draft.routing.mode === value} onChange={() => update({ routing: { mode: value } })} />{label}
        </label>)}
      </div>
    </fieldset>
    <label>Ollama 地址<input type="url" value={draft.ollama.baseUrl} onChange={(e) => update({ ollama: { ...draft.ollama, baseUrl: e.target.value } })} /></label>
    <label>本地模型<input value={draft.ollama.model} onChange={(e) => update({ ollama: { ...draft.ollama, model: e.target.value } })} /></label>
    <button type="button" onClick={save} disabled={mutation.isPending}>{mutation.isPending ? "保存中…" : "保存运行设置"}</button>
    {mutation.isError && <p role="alert">{clientErrorMessage(mutation.error)}</p>}
    {mutation.isSuccess && <p role="status">运行设置已保存</p>}
    <OllamaStatusCard />
    <OllamaPullForm />
  </div>;
}
