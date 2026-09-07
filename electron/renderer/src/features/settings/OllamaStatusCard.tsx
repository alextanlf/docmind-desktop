import type { OllamaModelsView, OllamaStatusView } from "../../../../shared/contracts";
import { useOllamaModelsQuery, useOllamaStatusQuery } from "./settings.queries";

export function OllamaStatusCard({ status: suppliedStatus, models: suppliedModels }: { status?: OllamaStatusView; models?: OllamaModelsView } = {}) {
  const status = useOllamaStatusQuery(!suppliedStatus);
  const models = useOllamaModelsQuery(!suppliedModels && Boolean(status.data?.available));
  const statusData = suppliedStatus ?? status.data;
  const modelsData = suppliedModels ?? models.data;
  if (suppliedStatus && !statusData?.available) return <div role="status" className="ollama-status-card"><strong>Ollama 未运行</strong><p>请启动本机 Ollama 服务后重新检查。</p></div>;
  if (!suppliedStatus && status.isPending) return <div role="status">正在检查 Ollama…</div>;
  if (!statusData?.available) return <div role="status" className="ollama-status-card"><strong>Ollama 未运行</strong><p>请启动本机 Ollama 服务后重新检查。</p></div>;
  const connectedStatus = statusData;
  return <div className="ollama-status-card" aria-live="polite">
    <p>Ollama 已连接{connectedStatus.version ? `（${connectedStatus.version}）` : ""}</p>
    {modelsData?.models.length ? <ul aria-label="已安装模型">{modelsData.models.map((model) => <li key={model.name}>{model.name}<span className="model-source-badge">本地</span></li>)}</ul> : <p>模型尚未安装</p>}
  </div>;
}
