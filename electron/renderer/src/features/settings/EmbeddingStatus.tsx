import { LoaderCircle, Play } from "lucide-react";
import { appQueryClient } from "../../app/query-client";
import { StatusBadge } from "../../components/StatusBadge";
import { clientErrorMessage, settingsKeys, useEmbeddingStatusQuery } from "./settings.queries";

// 模型随应用内置（打包脚本 package-local.sh 缺模型直接拒绝打包），
// 所以这里的文案一律用「加载 / 准备」，**不要写「下载」或任何体积数字** ——
// 那会让用户误以为要额外下载几百 MB。`state="downloading"` 是后端在
// ensure_ready() 里真实设置的过渡态（embedding.py:54），表示「正在从内置目录
// 加载到内存」，对应徽章用「加载中」而非「下载中」。
const STATUS = {
  unavailable: { label: "未加载", tone: "neutral" },
  downloading: { label: "加载中", tone: "pending" },
  ready: { label: "已就绪", tone: "success" },
  error: { label: "加载失败", tone: "error" },
} as const;

// 后端启动时若找不到内置模型才可能是「未缓存」，此时才需要提示重新准备。
const isCached = (state: string, message: string) =>
  state === "unavailable" && message.includes("已缓存");

export function EmbeddingStatus() {
  const query = useEmbeddingStatusQuery();

  async function prepare() {
    try {
      const status = await window.docmind.embedding.prepare();
      appQueryClient.setQueryData(settingsKeys.embedding, status);
    } catch (error) {
      appQueryClient.setQueryData(settingsKeys.embedding, {
        state: "error",
        // 不猜模型名：写死一个与实际不符的名字比留空更容易误导排查。
        modelName: query.data?.modelName ?? "",
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
  const cached = isCached(status.state, status.message);
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
        <p>
          {cached
            ? "模型已随应用内置，加载到内存后即可用于检索"
            : "首次导入文档时会自动加载，无需额外下载"}
        </p>
        {status.state === "error" ? (
          <p className="error-copy" role="alert">
            {status.message}
          </p>
        ) : null}
        {status.state === "downloading" ? (
          <div className="progress-line">
            <progress
              aria-label="Embedding 加载进度"
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
          <Play aria-hidden="true" size={16} />
          {status.state === "error" ? "重新加载" : "加载模型"}
        </button>
      ) : status.state === "downloading" ? (
        <LoaderCircle aria-hidden="true" className="spin status-spinner" size={18} />
      ) : null}
    </div>
  );
}
