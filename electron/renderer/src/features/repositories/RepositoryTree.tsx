import { ChevronDown, ChevronRight, FilePlus2, FolderPlus, RefreshCw } from "lucide-react";
import { useState } from "react";
import { clientErrorMessage } from "../settings/settings.queries";
import { ConflictList } from "./ConflictList";
import {
  useDocumentsQuery,
  useRepositoriesQuery,
  useRepositorySyncQuery,
  useSyncRepositoryMutation,
} from "./repository.queries";

type RepositoryTreeProps = {
  onOpenDocument?: (documentId: string, repositoryId: string) => void;
};

function RepositoryDocuments({
  repositoryId,
  onOpenDocument,
}: {
  repositoryId: string;
  onOpenDocument?: (documentId: string, repositoryId: string) => void;
}) {
  const documents = useDocumentsQuery(repositoryId);

  if (documents.isPending) return <p className="tree-message">正在读取文档…</p>;
  if (documents.isError) {
    return (
      <div className="tree-error" role="alert">
        <span>{clientErrorMessage(documents.error)}</span>
        <button className="tree-retry" onClick={() => void documents.refetch()} type="button">
          重新加载
        </button>
      </div>
    );
  }
  if (documents.data.length === 0) return <p className="tree-message">暂无文档</p>;
  return (
    <ul className="document-tree-list">
      {documents.data.map((document) => (
        <li key={document.id}>
          <button onClick={() => onOpenDocument?.(document.id, repositoryId)} type="button">
            {document.title}
          </button>
          {document.remoteDeleted ? <span className="remote-deleted-badge">远端已删除</span> : null}
        </li>
      ))}
    </ul>
  );
}

function RepositorySyncButton({ repositoryId }: { repositoryId: string }) {
  const sync = useRepositorySyncQuery(repositoryId);
  const mutation = useSyncRepositoryMutation(repositoryId);
  return (
    <button
      aria-label="立即同步"
      className="tree-icon-button"
      disabled={mutation.isPending}
      onClick={() => mutation.mutate()}
      title={sync.data?.lastSyncedAt ? `上次同步：${sync.data.lastSyncedAt}` : "立即同步"}
      type="button"
    >
      <RefreshCw aria-hidden="true" size={14} />
    </button>
  );
}

export function RepositoryTree({ onOpenDocument }: RepositoryTreeProps) {
  const repositories = useRepositoriesQuery();
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [createError, setCreateError] = useState("");

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
              <button
                aria-expanded={expanded}
                aria-label={`${expanded ? "收起" : "展开"} ${repository.name}`}
                className="repository-tree-button"
                onClick={() => setExpandedId(expanded ? null : repository.id)}
                type="button"
              >
                {expanded ? (
                  <ChevronDown aria-hidden="true" size={15} />
                ) : (
                  <ChevronRight aria-hidden="true" size={15} />
                )}
                <span>{repository.name}</span>
                <small>{repository.documentCount}</small>
              </button>
              <RepositorySyncButton repositoryId={repository.id} />
              {expanded ? (
                <>
                  <RepositoryDocuments
                    onOpenDocument={onOpenDocument}
                    repositoryId={repository.id}
                  />
                  <ConflictList repositoryId={repository.id} />
                </>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
