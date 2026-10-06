import type { LocalModelsView, LocalModelStatusView } from "../../../../shared/contracts";
import { useLocalModelsQuery, useLocalModelStatusQuery } from "./settings.queries";

export function LocalModelStatusCard({
  status: suppliedStatus,
  models: suppliedModels,
}: { status?: LocalModelStatusView; models?: LocalModelsView } = {}) {
  const status = useLocalModelStatusQuery(!suppliedStatus);
  const models = useLocalModelsQuery(!suppliedModels && Boolean(status.data?.available));
  const statusData = suppliedStatus ?? status.data;
  const modelsData = suppliedModels ?? models.data;

  const unavailable = (
    <div role="status" className="local-model-status-card">
      <strong>本地模型服务未运行</strong>
      <p>请启动本机的推理服务（Ollama、LM Studio 等）后重新检查。</p>
    </div>
  );
  if (suppliedStatus && !statusData?.available) return unavailable;
  if (!suppliedStatus && status.isPending) return <div role="status">正在检查本地模型服务…</div>;
  if (!statusData?.available) return unavailable;

  return (
    <div className="local-model-status-card" aria-live="polite">
      <p>本地模型服务已连接（{statusData.baseUrl}）</p>
      {modelsData?.models.length ? (
        <ul aria-label="服务已加载的模型">
          {modelsData.models.map((model) => (
            <li key={model.id}>
              {model.label}
              <span className="model-source-badge">本地</span>
            </li>
          ))}
        </ul>
      ) : (
        <p>
          服务未加载任何模型。DocMind 不提供模型下载，请在所用工具中加载模型后再选择。
        </p>
      )}
    </div>
  );
}
