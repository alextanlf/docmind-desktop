import {
  ChevronDown,
  ChevronRight,
  FilePlus2,
  FolderPlus,
  Pencil,
  RefreshCw,
  RotateCcw,
  Trash2,
} from "lucide-react";
import { useState } from "react";
import type { RemoteProviderCapabilities, Repository } from "../../../../shared/contracts";
import { ContextMenuTrigger, type ContextMenuItem } from "../../components/ContextMenu";
import { clientErrorMessage, useRemoteProvidersQuery } from "../settings/settings.queries";
import { ConflictList } from "./ConflictList";
import { DeleteRepositoryDialog } from "./DeleteRepositoryDialog";
import { RenameDialog } from "./RenameDialog";
import { RepositoryDocuments } from "./RepositoryDocuments";
import {
  useRemovedRepositoriesQuery,
  useRenameRepositoryMutation,
  useRepositoriesQuery,
  useRepositorySyncQuery,
  useRestoreRepositoryMutation,
  useSyncRepositoryMutation,
  useUpdateRepositoryMutation,
} from "./repository.queries";

type RepositoryTreeProps = {
  onOpenDocument?: (documentId: string, repositoryId: string) => void;
};

function RepositoryParentBinding({
  repositoryId,
  initialValue,
}: {
  repositoryId: string;
  initialValue: string | null;
}) {
  const [value, setValue] = useState(initialValue ?? "");
  const [error, setError] = useState("");
  const update = useUpdateRepositoryMutation();

  return (
    <form
      className="repository-parent-form"
      onSubmit={(event) => {
        event.preventDefault();
        setError("");
        update.mutate(
          { repositoryId, remoteParentId: value.trim() || null },
          { onError: (mutationError) => setError(clientErrorMessage(mutationError)) },
        );
      }}
    >
      <label htmlFor={`repository-parent-${repositoryId}`}>写入目录</label>
      <input
        id={`repository-parent-${repositoryId}`}
        onChange={(event) => setValue(event.target.value)}
        placeholder="父节点 Token，留空写入根目录"
        value={value}
      />
      <button className="tree-retry" disabled={update.isPending} type="submit">
        保存目录
      </button>
      {error ? (
        <p className="tree-error" role="alert">
          {error}
        </p>
      ) : null}
    </form>
  );
}

/**
 * 立即同步。
 *
 * 不再是行内按钮：行里平铺三个图标正是这次要收掉的东西，而且它只在悬停时才有意义。
 * 三个入口（右键、「⋯」、Shift+F10）共用的那一份菜单里都有它。
 */
function useSyncRepository(repositoryId: string) {
  const sync = useRepositorySyncQuery(repositoryId);
  const mutation = useSyncRepositoryMutation(repositoryId);
  return {
    pending: mutation.isPending,
    title: sync.data?.lastSyncedAt ? `上次同步：${sync.data.lastSyncedAt}` : "立即同步",
    syncNow: () => mutation.mutate(),
  };
}

/**
 * 已从 DocMind 移除的远端知识库。
 *
 * 没有这一段，用户点完「移除知识库」就再也找不回来了 —— 远端内容其实一直在，
 * 而界面上没有任何线索能让他把条目弄回来。所以恢复入口必须就长在移除它的地方。
 */
function RemovedRepositories({
  providerLabel,
}: {
  providerLabel: (provider: string | null | undefined) => string;
}) {
  const removed = useRemovedRepositoriesQuery();
  const restore = useRestoreRepositoryMutation();
  const [error, setError] = useState("");
  const [pendingId, setPendingId] = useState<string | null>(null);

  if (removed.isPending || removed.isError || removed.data.length === 0) return null;
  return (
    <div className="removed-repository-section">
      <div className="sidebar-section-title">
        <span>已移除的知识库</span>
      </div>
      <ul className="removed-repository-list">
        {removed.data.map((item) => (
          <li key={item.id}>
            <span title={item.name}>{item.name}</span>
            <small>{providerLabel(item.provider)}</small>
            <button
              aria-label={`恢复 ${item.name}`}
              className="tree-retry"
              disabled={restore.isPending}
              onClick={() => {
                setError("");
                setPendingId(item.id);
                restore.mutate(item.id, {
                  onError: (cause) => setError(clientErrorMessage(cause)),
                  onSettled: () => setPendingId(null),
                });
              }}
              type="button"
            >
              <RotateCcw aria-hidden="true" size={13} />
              {pendingId === item.id ? "恢复中…" : "恢复"}
            </button>
          </li>
        ))}
      </ul>
      {error ? (
        <p className="tree-error" role="alert">
          {error}
        </p>
      ) : null}
      <p className="tree-message">恢复后点「立即同步」把文档拉回来。</p>
    </div>
  );
}

/**
 * 侧边栏里的一行知识库。
 *
 * 行里现在只有名字、来源与篇数 —— 立即同步、重命名、移除都在这一行的菜单里（右键、
 * 「⋯」、Shift+F10 三种入口共用）。原先三个图标平铺在每行右侧，目录一多就只剩图标
 * 可看，这正是要收掉的东西。
 *
 * 自己持有"展开"与同步 mutation：`useSyncRepository` 是 hook，不能在一张列表的
 * `map` 回调里调用。
 */
function RepositoryRow({
  repository,
  expanded,
  capabilities,
  sourceLabel,
  onToggle,
  onOpenDocument,
  onRename,
  onRemove,
}: {
  repository: Repository;
  expanded: boolean;
  capabilities: RemoteProviderCapabilities | undefined;
  /** 来源的显示名（本地 / 语雀 / 飞书…），由列表算好传进来。 */
  sourceLabel: string;
  onToggle: () => void;
  onOpenDocument?: (documentId: string, repositoryId: string) => void;
  onRename: () => void;
  onRemove: () => void;
}) {
  const sync = useSyncRepository(repository.id);
  const items: ContextMenuItem[] = [
    ...(repository.provider
      ? [
          {
            label: "立即同步",
            text: sync.pending ? "同步中…" : "立即同步",
            icon: <RefreshCw aria-hidden="true" size={14} />,
            onSelect: sync.syncNow,
            disabled: sync.pending,
          },
        ]
      : []),
    {
      label: `重命名 ${repository.name}`,
      text: "重命名",
      icon: <Pencil aria-hidden="true" size={14} />,
      onSelect: onRename,
    },
    {
      label: `移除 ${repository.name}`,
      text: "移除知识库…",
      icon: <Trash2 aria-hidden="true" size={14} />,
      onSelect: onRemove,
      danger: true,
    },
  ];

  return (
    <>
      <div className="tree-row">
        <button
          aria-expanded={expanded}
          aria-label={`${expanded ? "收起" : "展开"} ${repository.name}`}
          className="repository-tree-button"
          onClick={onToggle}
          type="button"
        >
          {expanded ? (
            <ChevronDown aria-hidden="true" size={15} />
          ) : (
            <ChevronRight aria-hidden="true" size={15} />
          )}
          <span>{repository.name}</span>
          <small>
            {sourceLabel} · {repository.documentCount}
          </small>
        </button>
        <ContextMenuTrigger
          items={items}
          label="知识库操作"
          triggerLabel={`${repository.name} 的操作`}
        >
          <span />
        </ContextMenuTrigger>
      </div>
      {expanded ? (
        <>
          <RepositoryDocuments
            onOpenDocument={onOpenDocument}
            remoteRenameSupported={capabilities?.documentRename ?? false}
            repositoryId={repository.id}
            repositoryName={repository.name}
          />
          {repository.provider ? <ConflictList repositoryId={repository.id} /> : null}
          {capabilities?.parentNodeWrite ? (
            <RepositoryParentBinding
              key={`${repository.id}:${repository.remoteParentId ?? ""}`}
              initialValue={repository.remoteParentId ?? null}
              repositoryId={repository.id}
            />
          ) : null}
        </>
      ) : null}
    </>
  );
}

export function RepositoryTree({ onOpenDocument }: RepositoryTreeProps) {
  const repositories = useRepositoriesQuery();
  const providers = useRemoteProvidersQuery();
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [createError, setCreateError] = useState("");
  // 正在确认移除的那一个。存整个 Repository 而不是 id：确认框要显示名字与
  // provider，而从 id 反查会在列表被刷新（例如同步回来）时落空。
  const [removing, setRemoving] = useState<Repository | null>(null);
  // 正在改名的那一个。同样存整个对象：改名对话框的初值是当时那个名字。
  const [renaming, setRenaming] = useState<Repository | null>(null);
  const rename = useRenameRepositoryMutation();

  // Provider display names come from the registry summary; unknown providers
  // fall back to their raw name, local repositories show 本地.
  const providerLabel = (provider: string | null | undefined) => {
    if (!provider) return "本地";
    return providers.data?.find((entry) => entry.name === provider)?.label ?? provider;
  };
  const providerCapabilities = (provider: string | null | undefined) =>
    providers.data?.find((entry) => entry.name === provider)?.capabilities;

  async function createRepository() {
    const trimmed = name.trim();
    if (!trimmed) return;
    setCreateError("");
    try {
      const created = await window.docmind.repositories.create({ name: trimmed });
      await repositories.refetch();
      setExpandedId(created.id);
      setName("");
      setCreating(false);
    } catch (error) {
      setCreateError(clientErrorMessage(error));
    }
  }

  if (repositories.isPending) return <p className="tree-message">正在读取知识库…</p>;
  if (repositories.isError) {
    return (
      <div className="tree-error" role="alert">
        <span>{clientErrorMessage(repositories.error)}</span>
        <button className="tree-retry" onClick={() => void repositories.refetch()} type="button">
          <RefreshCw aria-hidden="true" size={14} />
          重新加载
        </button>
      </div>
    );
  }

  return (
    <div className="repository-tree">
      <div className="sidebar-section-title">
        <span>知识库</span>
        <button
          aria-label="新建知识库"
          className="tree-icon-button"
          onClick={() => setCreating(true)}
          title="新建知识库"
          type="button"
        >
          <FolderPlus aria-hidden="true" size={15} />
        </button>
      </div>
      {creating ? (
        <form
          className="tree-create-form"
          onSubmit={(event) => {
            event.preventDefault();
            void createRepository();
          }}
        >
          <label className="sr-only" htmlFor="repository-name">
            知识库名称
          </label>
          <input
            autoFocus
            id="repository-name"
            onChange={(event) => setName(event.target.value)}
            placeholder="知识库名称"
            value={name}
          />
          <button
            aria-label="保存知识库"
            className="tree-icon-button"
            title="保存知识库"
            type="submit"
          >
            <FilePlus2 aria-hidden="true" size={15} />
          </button>
          {createError ? (
            <p className="tree-error" role="alert">
              {createError}
            </p>
          ) : null}
        </form>
      ) : null}
      <ul className="repository-tree-list">
        {repositories.data.map((repository) => {
          const expanded = expandedId === repository.id;
          return (
            <li key={repository.id}>
              <RepositoryRow
                capabilities={providerCapabilities(repository.provider)}
                expanded={expanded}
                onOpenDocument={onOpenDocument}
                onRemove={() => setRemoving(repository)}
                onRename={() => setRenaming(repository)}
                onToggle={() => setExpandedId(expanded ? null : repository.id)}
                repository={repository}
                sourceLabel={providerLabel(repository.provider)}
              />
            </li>
          );
        })}
      </ul>
      <RemovedRepositories providerLabel={providerLabel} />
      {renaming ? (
        <RenameDialog
          initialValue={renaming.name}
          key={`rename:${renaming.id}`}
          kind="repository"
          onClose={() => setRenaming(null)}
          onSubmit={async (name) => {
            const result = await rename.mutateAsync({ repositoryId: renaming.id, name });
            // 远端没跟着改时**不关窗口**：让用户当场看到"远端名字没变"这件事，
            // 而不是关掉之后再刷新列表，发现名字自己变回去了。
            return result.scope === "remote"
              ? null
              : `已在 DocMind 中改名为「${result.repository.name}」，但远端知识库的名字没有跟着改，之后同步可能把它改回去。`;
          }}
          remoteRenameSupported={providerCapabilities(renaming.provider)?.repositoryRename ?? false}
        />
      ) : null}
      {removing ? (
        <DeleteRepositoryDialog
          key={removing.id}
          onClose={() => setRemoving(null)}
          providerLabel={providerLabel(removing.provider)}
          repository={removing}
        />
      ) : null}
    </div>
  );
}
