import { Trash2 } from "lucide-react";
import { Fragment, useState, type ReactNode } from "react";
import type { DocumentSummary } from "../../../../shared/contracts";
import { clientErrorMessage } from "../settings/settings.queries";
import {
  buildDocumentTree,
  documentsUnder,
  hasDirectories,
  selectionState,
  type DocumentTreeNode,
} from "./documentTree";
import { RemovedDocuments } from "./RemovedDocuments";
import { RemoveDocumentsDialog } from "./RemoveDocumentsDialog";
import { useDocumentsQuery, useRemoveDocumentsMutation } from "./repository.queries";

function DocumentRow({
  document,
  selected,
  onToggle,
  onOpen,
  repositoryId,
}: {
  document: DocumentSummary;
  selected: boolean;
  onToggle: () => void;
  onOpen?: (documentId: string, repositoryId: string) => void;
  repositoryId: string;
}) {
  return (
    <li>
      <label className="document-select">
        <input
          aria-label={`选择 ${document.title}`}
          checked={selected}
          onChange={onToggle}
          type="checkbox"
        />
      </label>
      <button onClick={() => onOpen?.(document.id, repositoryId)} type="button">
        {document.title}
      </button>
      {document.remoteDeleted ? <span className="remote-deleted-badge">远端已删除</span> : null}
    </li>
  );
}

/**
 * 目录行：勾它就是勾中它子树里的全部文档。
 *
 * 这是「按目录移除」的全部机制 —— 不需要新的后端端点，前缀选出来的那些文档走
 * 已有的批量移除。方框的三态必须如实：只选了部分子文档时显示"全选"会让用户以为
 * 整个目录都会被清掉。
 */
function DirectoryRow({
  node,
  depth,
  selected,
  onToggle,
  collapsed,
  onToggleCollapsed,
}: {
  node: DocumentTreeNode;
  depth: number;
  selected: ReadonlySet<string>;
  onToggle: (documents: DocumentSummary[]) => void;
  collapsed: boolean;
  onToggleCollapsed: () => void;
}) {
  const state = selectionState(node, selected);
  const documents = documentsUnder(node);
  return (
    <li className="document-directory-row">
      <button
        aria-expanded={!collapsed}
        aria-label={`${collapsed ? "展开" : "收起"}目录 ${node.path.join(" / ")}`}
        className="document-directory-toggle"
        onClick={onToggleCollapsed}
        type="button"
      >
        {collapsed ? "▸" : "▾"}
      </button>
      <label className="document-select">
        <input
          aria-label={`选择目录 ${node.path.join(" / ")}`}
          checked={state === "all"}
          onChange={() => onToggle(documents)}
          ref={(element) => {
            if (element) element.indeterminate = state === "some";
          }}
          type="checkbox"
        />
      </label>
      <span className="document-directory-name" style={{ paddingLeft: depth * 2 }}>
        {node.name}
      </span>
      <small>
        {node.total} 篇
        {state === "some" ? ` · 已选 ${documents.filter((d) => selected.has(d.id)).length}` : ""}
      </small>
    </li>
  );
}

type RepositoryDocumentsProps = {
  repositoryId: string;
  repositoryName: string;
  onOpenDocument?: (documentId: string, repositoryId: string) => void;
};

/**
 * 一个知识库的文档列表，带多选与批量移除。
 *
 * 列表上的移除**只删本地**：这里是从远端不断更新的知识库里做"我处理过的先挪走"
 * 的日常动作，顺手删掉用户在语雀/飞书上的原文是不可接受的默认。要删远端原文去
 * 文档详情页，那里是刻意、明确、带二次确认的选择。
 */
export function RepositoryDocuments({
  repositoryId,
  repositoryName,
  onOpenDocument,
}: RepositoryDocumentsProps) {
  const documents = useDocumentsQuery(repositoryId);
  const remove = useRemoveDocumentsMutation(repositoryId);
  const [selected, setSelected] = useState<string[]>([]);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");
  // 折叠的目录。**必须和其余 hooks 一起放在 early return 之前** —— 放到下面会让
  // "正在读取 → 已加载"那次渲染多出一个 hook，React 直接抛
  // "Rendered more hooks than during the previous render"。
  const [collapsedDirectories, setCollapsedDirectories] = useState<string[]>([]);

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

  const items: DocumentSummary[] = documents.data;
  // 选中的可能已经被删掉了（例如刚移除过），所以每次都按当前列表求交集，
  // 而不是相信 state 里的 id 还有效。
  const selectedDocuments = items.filter((document) => selected.includes(document.id));
  const toggle = (documentId: string) =>
    setSelected((current) =>
      current.includes(documentId)
        ? current.filter((id) => id !== documentId)
        : [...current, documentId],
    );
  // 有层级才画树。provider 不提供路径时所有 path 都是空数组，这里就是 null，
  // 界面维持原样的平铺列表 —— 不给自己编一层目录出来。
  // 有层级才画树。provider 不提供路径时所有 path 都是空数组，这里就是 null，
  // 界面维持原样的平铺列表 —— 不给自己编一层目录出来。
  const tree = hasDirectories(items) ? buildDocumentTree(items) : null;

  function toggleMany(documents: DocumentSummary[]) {
    const ids = documents.map((document) => document.id);
    const allSelected = ids.every((id) => selected.includes(id));
    setSelected((current) =>
      allSelected ? current.filter((id) => !ids.includes(id)) : [...new Set([...current, ...ids])],
    );
  }

  function renderNode(node: DocumentTreeNode, depth: number): ReactNode[] {
    return [
      ...node.directories.map((child) => {
        const key = child.path.join(" / ");
        const isCollapsed = collapsedDirectories.includes(key);
        return (
          <Fragment key={`dir:${key}`}>
            <DirectoryRow
              collapsed={isCollapsed}
              depth={depth}
              node={child}
              onToggle={toggleMany}
              onToggleCollapsed={() =>
                setCollapsedDirectories((current) =>
                  current.includes(key)
                    ? current.filter((item) => item !== key)
                    : [...current, key],
                )
              }
              selected={new Set(selected)}
            />
            {isCollapsed ? null : renderNode(child, depth + 1)}
          </Fragment>
        );
      }),
      ...node.documents.map((document) => (
        <DocumentRow
          document={document}
          key={document.id}
          onOpen={onOpenDocument}
          onToggle={() => toggle(document.id)}
          repositoryId={repositoryId}
          selected={selected.includes(document.id)}
        />
      )),
    ];
  }

  return (
    <div className="document-list-section">
      {items.length === 0 ? (
        <p className="tree-message">暂无文档</p>
      ) : (
        <>
          <div className="document-selection-bar">
            <label className="document-select-all">
              <input
                aria-label="全选文档"
                checked={selectedDocuments.length === items.length}
                onChange={(event) =>
                  setSelected(event.target.checked ? items.map((d) => d.id) : [])
                }
                type="checkbox"
              />
              <span>全选</span>
            </label>
            <button
              aria-label={`移除所选的 ${selectedDocuments.length} 篇文档`}
              className="tree-retry"
              disabled={selectedDocuments.length === 0 || remove.isPending}
              onClick={() => {
                setError("");
                setConfirming(true);
              }}
              type="button"
            >
              <Trash2 aria-hidden="true" size={13} />
              移除所选 ({selectedDocuments.length})
            </button>
          </div>
          {tree ? (
            <ul className="document-tree-list document-tree">{renderNode(tree, 0)}</ul>
          ) : (
            <ul className="document-tree-list">
              {items.map((document) => (
                <DocumentRow
                  document={document}
                  key={document.id}
                  onOpen={onOpenDocument}
                  onToggle={() => toggle(document.id)}
                  repositoryId={repositoryId}
                  selected={selected.includes(document.id)}
                />
              ))}
            </ul>
          )}
        </>
      )}
      <RemovedDocuments repositoryId={repositoryId} />
      {confirming ? (
        <RemoveDocumentsDialog
          documents={selectedDocuments}
          error={error}
          onClose={() => setConfirming(false)}
          onConfirm={() =>
            remove.mutate(
              selectedDocuments.map((document) => document.id),
              {
                onSuccess: () => {
                  setSelected([]);
                  setConfirming(false);
                },
                onError: (cause) => setError(clientErrorMessage(cause)),
              },
            )
          }
          pending={remove.isPending}
          repositoryName={repositoryName}
        />
      ) : null}
    </div>
  );
}
