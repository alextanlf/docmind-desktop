import { FolderOpen } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { clientErrorMessage, useRemoteProvidersQuery } from "../settings/settings.queries";
import {
  ScanProgressPayloadSchema,
  ScanResultPayloadSchema,
  type Repository,
} from "../../../../shared/contracts";
import { TaskProgress } from "../../components/TaskProgress";
import { useCreateBatchMutation } from "./batch-import.queries";

/**
 * 订阅标识：与聊天流同一条约定 —— 由调用方生成，主进程只负责沿这个 id 回事件。
 *
 * 退路那串固定 UUID 是给"环境里没有 crypto"的情形用的（与 ChatPanel 同名函数一致，
 * 那里已经踩过一次）。
 */
function createRequestId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function")
    return crypto.randomUUID();
  return "00000000-0000-4000-8000-000000000030";
}

export function BatchSourceStep({
  repositories,
  onCreated,
}: {
  repositories: Repository[];
  onCreated: (batchId: string, discoveryVersion: number) => void;
}) {
  const [kind, setKind] = useState<"staged_directory" | "web" | "remote_repository">(
    "staged_directory",
  );
  const [entryUrl, setEntryUrl] = useState("");
  const [maxDepth, setMaxDepth] = useState(5);
  const [maxPages, setMaxPages] = useState(200);
  const [useSitemap, setUseSitemap] = useState(true);
  const [repositoryId, setRepositoryId] = useState(repositories[0]?.id ?? "");
  const [error, setError] = useState("");
  const providers = useRemoteProvidersQuery();
  // Remote pulls can only target provider-bound repositories; the option
  // label carries the provider so simultaneous multi-provider bindings stay
  // distinguishable.
  const remoteRepositories = repositories.filter((r) => r.provider);
  const providerLabel = (provider: string | null | undefined) => {
    if (!provider) return "本地";
    return providers.data?.find((entry) => entry.name === provider)?.label ?? provider;
  };
  const selectableRepositories = kind === "remote_repository" ? remoteRepositories : repositories;
  useEffect(() => {
    if (!repositoryId && repositories[0]) setRepositoryId(repositories[0].id);
  }, [repositoryId, repositories]);
  const createBatch = useCreateBatchMutation();
  /**
   * 扫描进度。null 表示没在扫。
   *
   * 扫描要把目录里每个文件完整读一遍算摘要（不复制），几百个文件就是几分钟 —— 所以它
   * 在后台跑、边跑边报"已扫描 n/N"，而不是让这个按钮停在"正在读取…"上。
   */
  const [scan, setScan] = useState<{ scanned: number; total: number } | null>(null);
  const scanSubscription = useRef<{ cancel(): void; detach(): void } | null>(null);

  function chooseDirectory() {
    const selectedRepositoryId = repositoryId || repositories[0]?.id;
    if (!selectedRepositoryId) {
      setError("请选择目标知识库");
      return;
    }
    setError("");
    setScan(null);
    // 与聊天流同一套订阅机制：先订阅、再让主进程开扫；进度与结果都从这条流回来。
    const subscription = window.docmind.sources.scanDirectory(createRequestId(), (event) => {
      if (event.type === "progress") {
        const payload = ScanProgressPayloadSchema.safeParse(event.payload);
        if (payload.success) setScan(payload.data);
        return;
      }
      if (event.type === "error") {
        setScan(null);
        setError(clientErrorMessage(event.payload));
        return;
      }
      // done：要么拿到一份清单（继续建批次），要么是用户取消（什么都不做）。
      setScan(null);
      const result = ScanResultPayloadSchema.safeParse(event.payload);
      if (!result.success || !result.data.collection) return;
      void createBatch
        .mutateAsync({
          kind: "staged_directory",
          sourceId: result.data.collection.collectionId,
          repositoryId: selectedRepositoryId,
        })
        .then((batch) => onCreated(batch.id, batch.discoveryVersion))
        .catch((cause) => setError(clientErrorMessage(cause)));
    });
    scanSubscription.current = subscription;
  }

  // 组件消失就**取消**（不只是停止监听）：扫描在后台读用户磁盘，没人要的结果不该继续跑完
  // —— 否则关掉对话框之后主进程还会读完整个目录，并留下一份没人引用的清单。
  useEffect(() => () => scanSubscription.current?.cancel(), []);
  async function createRemote() {
    const selectedRepositoryId = repositoryId || repositories[0]?.id;
    if (!selectedRepositoryId) {
      setError("请选择目标知识库");
      return;
    }
    if (kind === "web" && !/^https?:\/\//i.test(entryUrl)) {
      setError("请输入有效的入口网址");
      return;
    }
    setError("");
    try {
      const input =
        kind === "web"
          ? {
              kind: "web" as const,
              entryUrl: entryUrl.trim(),
              repositoryId: selectedRepositoryId,
              maxDepth: Math.min(5, Math.max(0, maxDepth)),
              maxPages: Math.min(200, Math.max(1, maxPages)),
              useSitemap,
            }
          : { kind: "remote_repository" as const, repositoryId: selectedRepositoryId };
      // 不再 `as never`：CreateBatchInputSchema 已是覆盖后端四种 kind 的
      // discriminatedUnion，类型能自己推出来。那个 as never 是在掩盖
      // 「schema 只认 staged_directory」的事实。
      const batch = await createBatch.mutateAsync(input);
      onCreated(batch.id, batch.discoveryVersion);
    } catch (cause) {
      setError(clientErrorMessage(cause));
    }
  }
  return (
    <div className="import-step batch-source-step">
      <div role="tablist" aria-label="批量导入来源" className="segmented-control">
        {(
          [
            ["staged_directory", "本地目录"],
            ["web", "网站"],
            ["remote_repository", "远程知识库"],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            role="tab"
            aria-selected={kind === value}
            className={kind === value ? "is-active" : ""}
            type="button"
            onClick={() => {
              setKind(value);
              setError("");
            }}
          >
            {label}
          </button>
        ))}
      </div>
      <label className="dialog-field">
        <span>目标知识库</span>
        <select
          aria-label="目标知识库"
          value={repositoryId}
          onChange={(e) => setRepositoryId(e.target.value)}
        >
          <option value="">请选择</option>
          {selectableRepositories.map((r) => (
            <option key={r.id} value={r.id}>
              {r.provider ? `${r.name}（${providerLabel(r.provider)}）` : r.name}
            </option>
          ))}
        </select>
      </label>
      {kind === "staged_directory" && scan !== null ? (
        // 扫描在后台跑，这里只显示进度并允许中止。取消之后不会留下半成品清单
        // （主进程会把 .partial 目录删掉），所以"取消"是干净的。
        <TaskProgress
          label="目录扫描进度"
          progress={scan.total > 0 ? Math.floor((scan.scanned / scan.total) * 100) : null}
          progressText={`已扫描 ${scan.scanned}/${scan.total}`}
          statusText="只读取文件算摘要，不复制"
          onCancel={() => scanSubscription.current?.cancel()}
        />
      ) : kind === "staged_directory" ? (
        <button
          className="button button-secondary"
          disabled={createBatch.isPending}
          onClick={() => chooseDirectory()}
          type="button"
        >
          <FolderOpen aria-hidden="true" size={16} />
          {createBatch.isPending ? "正在创建批次…" : "选择目录"}
        </button>
      ) : null}
      {kind === "web" ? (
        <div className="remote-source-form">
          <label className="dialog-field">
            <span>入口网址</span>
            <input
              aria-label="入口网址"
              type="url"
              value={entryUrl}
              onChange={(e) => setEntryUrl(e.target.value)}
              placeholder="https://docs.example.com/"
            />
          </label>
          <label className="dialog-field">
            <span>最大深度</span>
            <input
              type="number"
              min={0}
              max={5}
              value={maxDepth}
              onChange={(e) => setMaxDepth(Number(e.target.value))}
            />
          </label>
          <label className="dialog-field">
            <span>最大页面数</span>
            <input
              type="number"
              min={1}
              max={200}
              value={maxPages}
              onChange={(e) => setMaxPages(Number(e.target.value))}
            />
          </label>
          <label>
            <input
              type="checkbox"
              checked={useSitemap}
              onChange={(e) => setUseSitemap(e.target.checked)}
            />{" "}
            使用 sitemap
          </label>
          <button
            className="button button-primary"
            disabled={createBatch.isPending}
            onClick={() => void createRemote()}
            type="button"
          >
            开始发现
          </button>
        </div>
      ) : null}
      {kind === "remote_repository" ? (
        <div className="remote-source-form">
          <p role="note">仅拉取，不会修改远程知识库</p>
          {remoteRepositories.length === 0 ? (
            <p role="note">暂无已绑定的远程知识库，请先在设置的插件页连接一个</p>
          ) : (
            <button
              className="button button-primary"
              disabled={createBatch.isPending}
              onClick={() => void createRemote()}
              type="button"
            >
              开始拉取
            </button>
          )}
        </div>
      ) : null}
      {error ? (
        <p className="editor-error" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
