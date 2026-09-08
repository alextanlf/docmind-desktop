import { Download, LoaderCircle, Play, RotateCcw } from "lucide-react";
import { appQueryClient } from "../../app/query-client";
import { StatusBadge } from "../../components/StatusBadge";
import { clientErrorMessage, settingsKeys, useEmbeddingStatusQuery } from "./settings.queries";

const STATUS = {
  unavailable: { label: "未加载", tone: "neutral" },
  downloading: { label: "下载中", tone: "pending" },
  ready: { label: "已就绪", tone: "success" },
  error: { label: "下载失败", tone: "error" },
} as const;

export function EmbeddingStatus() {
  const query = useEmbeddingStatusQuery();

  async function prepare() {
    try {
      const status = await window.docmind.embedding.prepare();
      appQueryClient.setQueryData(settingsKeys.embedding, status);
    } catch (error) {
      appQueryClient.setQueryData(settingsKeys.embedding, {
        state: "error",
        modelName: query.data?.modelName ?? "BAAI/bge-base-zh-v1.5",
        message: clientErrorMessage(error),
        progress: null,
      });
    }
  }

  if (query.isPending) return <p className="muted-row">正在读取 Embedding 状态…</p>;
  if (query.isError) {
    return (
      <div className="inline-error" role="alert">
        <span>{clientErrorMessage(query.error)}</span>
        <button className="button button-secondary" onClick={() => query.refetch()}>
          重新检查
        </button>
      </div>
    );
  }

  const status = query.data;
  const cached = status.state === "unavailable" && status.message.includes("已缓存");
  const presentation = {
    ...STATUS[status.state],
    label: cached ? "已缓存" : STATUS[status.state].label,
  };
  return (
    <div className="status-row">
      <div className="status-copy">
        <div className="status-title-line">
          <strong>{status.modelName}</strong>
          <StatusBadge label={presentation.label} tone={presentation.tone} />
        </div>
        <p>{cached ? "模型已缓存，无需重新下载" : "约 400 MB，首次导入前需要下载"}</p>
        {status.state === "error" ? (
          <p className="error-copy" role="alert">
            {status.message}
          </p>
        ) : null}
        {status.state === "downloading" ? (
          <div className="progress-line">
            <progress
              aria-label="Embedding 下载进度"
              aria-valuenow={status.progress ?? 0}
              max={100}
              value={status.progress ?? 0}
            />
            <span>{status.progress ?? 0}%</span>
          </div>
        ) : null}
      </div>
      {status.state === "unavailable" || status.state === "error" ? (
        <button className="button button-secondary" onClick={prepare}>
          {status.state === "error" ? (
            <RotateCcw aria-hidden="true" size={16} />
          ) : cached ? (
            <Play aria-hidden="true" size={16} />
          ) : (
            <Download aria-hidden="true" size={16} />
          )}
          {status.state === "error" ? "重试下载" : cached ? "加载模型" : "下载模型"}
        </button>
      ) : status.state === "downloading" ? (
        <LoaderCircle aria-hidden="true" className="spin status-spinner" size={18} />
      ) : null}
    </div>
  );
}
