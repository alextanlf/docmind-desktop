import { useState } from "react";
import type { SettingsView } from "../../../../shared/contracts";
import { useSaveWebSearchMutation } from "./settings.queries";

export function WebSearchSettings({ settings }: { settings: SettingsView }) {
  const save = useSaveWebSearchMutation();
  const [mode, setMode] = useState(settings.webSearch.mode);
  const [maxResults, setMaxResults] = useState(settings.webSearch.maxResults);
  const [apiKey, setApiKey] = useState("");
  const [clearApiKey, setClearApiKey] = useState(false);
  const submit = () =>
    save.mutate(
      { mode, maxResults, ...(clearApiKey ? { apiKey: "" } : apiKey ? { apiKey } : {}) },
      {
        onSuccess: () => {
          setApiKey("");
          setClearApiKey(false);
        },
      },
    );
  return (
    <div className="web-search-settings">
      <fieldset>
        <legend>联网策略</legend>
        {(
          [
            ["off", "关闭"],
            ["ask", "询问"],
            ["auto", "自动"],
          ] as const
        ).map(([value, label]) => (
          <label key={value}>
            <input
              checked={mode === value}
              name="web-search-mode"
              onChange={() => setMode(value)}
              type="radio"
            />
            {label}
          </label>
        ))}
      </fieldset>
      <label>
        最大结果数
        <input
          aria-label="最大搜索结果数"
          max={10}
          min={1}
          onChange={(event) => setMaxResults(Number(event.target.value))}
          type="number"
          value={maxResults}
        />
      </label>
      <label>
        Tavily API Key
        <input
          aria-label="Tavily API Key"
          autoComplete="off"
          disabled={clearApiKey}
          onChange={(event) => setApiKey(event.target.value)}
          placeholder={settings.webSearch.hasApiKey ? "已安全保存，留空则不修改" : "输入 API Key"}
          type="password"
          value={apiKey}
        />
      </label>
      {settings.webSearch.hasApiKey ? (
        <label>
          <input
            checked={clearApiKey}
            onChange={(event) => setClearApiKey(event.target.checked)}
            type="checkbox"
          />
          删除已保存的密钥
        </label>
      ) : null}
      <button
        className="button button-primary"
        disabled={save.isPending}
        onClick={submit}
        type="button"
      >
        保存联网设置
      </button>
      {save.isError ? <p role="alert">保存失败，请检查后重试。</p> : null}
      {save.isSuccess ? <p role="status">联网设置已保存。</p> : null}
    </div>
  );
}
