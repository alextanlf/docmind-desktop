import { FolderOpen } from "lucide-react";
import { useEffect, useState } from "react";
import { clientErrorMessage, useRemoteProvidersQuery } from "../settings/settings.queries";
import type { Repository } from "../../../../shared/contracts";
import { useCreateBatchMutation } from "./batch-import.queries";

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
  async function chooseDirectory() {
    const selectedRepositoryId = repositoryId || repositories[0]?.id;
    if (!selectedRepositoryId) {
      setError("请选择目标知识库");
      return;
    }
    setError("");
    try {
      const staged = await window.docmind.sources.stageDirectory();
      if (!staged) return;
      const batch = await createBatch.mutateAsync({
        kind: "staged_directory",
        sourceId: staged.collectionId,
        repositoryId: selectedRepositoryId,
      });
      onCreated(batch.id, batch.discoveryVersion);
    } catch (cause) {
      setError(clientErrorMessage(cause));
    }
  }
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
      {kind === "staged_directory" ? (
        <button
          className="button button-secondary"
          disabled={createBatch.isPending}
          onClick={() => void chooseDirectory()}
          type="button"
        >
          <FolderOpen aria-hidden="true" size={16} />
          {createBatch.isPending ? "正在读取…" : "选择目录"}
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
