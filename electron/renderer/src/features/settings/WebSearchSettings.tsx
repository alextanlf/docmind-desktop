import { useState } from "react";
import type { SettingsView } from "../../../../shared/contracts";
import { useSaveWebSearchMutation } from "./settings.queries";

export function WebSearchSettings({ settings }: { settings: SettingsView }) {
  const save = useSaveWebSearchMutation();
  const [mode, setMode] = useState(settings.webSearch.mode);
  const [maxResults, setMaxResults] = useState(settings.webSearch.maxResults);
  const [queryRewrite, setQueryRewrite] = useState(settings.webSearch.queryRewrite);
  const [searxngUrl, setSearxngUrl] = useState(settings.webSearch.searxngUrl);
  const [apiKey, setApiKey] = useState("");
  const [clearApiKey, setClearApiKey] = useState(false);
  const { hasApiKey, modelSearchLabel, freeFallbackAvailable } = settings.webSearch;
  const submit = () =>
    save.mutate(
      {
        mode,
        maxResults,
        queryRewrite,
        searxngUrl,
        ...(clearApiKey ? { apiKey: "" } : apiKey ? { apiKey } : {}),
      },
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
      <label className="web-search-rewrite">
        <input
          checked={queryRewrite}
          onChange={(event) => setQueryRewrite(event.target.checked)}
          type="checkbox"
        />
        智能改写搜索词（用当前模型生成 2-3 组查询，提升召回）
      </label>
      <section aria-label="搜索来源优先级" className="web-search-sources">
        <h4>搜索来源（按优先级自动切换）</h4>
        <ol>
          <li>
            <strong>模型内置联网</strong>
            <span>{modelSearchLabel || "当前模型不支持内置联网，自动跳过"}</span>
          </li>
          <li>
            <strong>Tavily</strong>
            <span>{hasApiKey ? "已配置，作为第二来源" : "未配置，自动跳过"}</span>
          </li>
          <li>
            <strong>SearXNG（自建）</strong>
            <span>{searxngUrl ? "已配置，作为第三来源" : "未配置，自动跳过"}</span>
          </li>
          <li>
            <strong>免费兜底</strong>
            <span>
              {freeFallbackAvailable ? "Bing / DuckDuckGo（无需配置，自动抓正文）" : "暂不可用"}
            </span>
          </li>
        </ol>
      </section>
      <details className="web-search-advanced">
        <summary>高级：自建 SearXNG / Tavily API Key</summary>
        <label>
          SearXNG 实例地址
          <input
            aria-label="SearXNG 实例地址"
            onChange={(event) => setSearxngUrl(event.target.value)}
            placeholder="https://searx.example.com"
            type="url"
            value={searxngUrl}
          />
          <small>需在实例的 settings.yml 中开启 JSON 输出（search.formats 加上 json）</small>
        </label>
        <label>
          Tavily API Key
          <input
            aria-label="Tavily API Key"
            autoComplete="off"
            disabled={clearApiKey}
            onChange={(event) => setApiKey(event.target.value)}
            placeholder={hasApiKey ? "已安全保存，留空则不修改" : "输入 API Key"}
            type="password"
            value={apiKey}
          />
        </label>
        {hasApiKey ? (
          <label>
            <input
              checked={clearApiKey}
              onChange={(event) => setClearApiKey(event.target.checked)}
              type="checkbox"
            />
            删除已保存的密钥
          </label>
        ) : null}
      </details>
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
