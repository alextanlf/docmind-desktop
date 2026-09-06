import { useOllamaModelsQuery, useOllamaStatusQuery } from "./settings.queries";

export function OllamaStatusCard() {
  const status = useOllamaStatusQuery(true);
  const models = useOllamaModelsQuery(Boolean(status.data?.available));
  if (status.isPending) return <div role="status">正在检查 Ollama…</div>;
  if (!status.data?.available) return <div role="status" className="ollama-status-card">Ollama 未运行，请启动本机 Ollama 服务。</div>;
  return <div className="ollama-status-card" aria-live="polite">
    <p>Ollama 已连接{status.data.version ? `（${status.data.version}）` : ""}</p>
    {models.data?.models.length ? <ul aria-label="已安装模型">{models.data.models.map((model) => <li key={model.name}>{model.name}<span className="model-source-badge">本地</span></li>)}</ul> : <p>模型尚未安装</p>}
  </div>;
}
