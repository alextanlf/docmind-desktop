import type { DocumentSummary } from "../../../../shared/contracts";

/**
 * 文档列表 → 目录树。
 *
 * 层级完全来自 `DocumentSummary.path`（远端目录路径），**不猜**：provider 没声明
 * `document_paths` 时每篇的 path 都是空数组，于是所有文档都落在根节点下、界面照旧
 * 平铺显示。这条边界很重要 —— 自己按标题前缀编一层出来，用户按目录清理时会漏
 * 文档，而"漏掉"在清理场景里等于没清。
 */
export type DocumentTreeNode = {
  /** 这一层的目录名；根节点为空串。 */
  name: string;
  /** 从根到这一层的完整路径，用于前缀匹配与稳定 key。 */
  path: string[];
  directories: DocumentTreeNode[];
  documents: DocumentSummary[];
  /** 这一棵子树里的文档总数（含子目录）。 */
  total: number;
};

function emptyNode(name: string, path: string[]): DocumentTreeNode {
  return { name, path, directories: [], documents: [], total: 0 };
}

export function buildDocumentTree(documents: DocumentSummary[]): DocumentTreeNode {
  const root = emptyNode("", []);
  const directories = new Map<string, DocumentTreeNode>([["", root]]);

  const directoryFor = (path: string[]): DocumentTreeNode => {
    const key = path.join("\u0000");
    const existing = directories.get(key);
    if (existing) return existing;
    const parent = directoryFor(path.slice(0, -1));
    const node = emptyNode(path[path.length - 1] ?? "", path);
    parent.directories.push(node);
    directories.set(key, node);
    return node;
  };

  for (const document of documents) {
    directoryFor(document.path ?? []).documents.push(document);
  }

  // 文档数自底向上累加。排序按名字，保证渲染顺序稳定（同步刷新前后不会跳）。
  const finalize = (node: DocumentTreeNode): number => {
    node.directories.sort((left, right) => left.name.localeCompare(right.name, "zh"));
    node.total =
      node.documents.length + node.directories.reduce((sum, child) => sum + finalize(child), 0);
    return node.total;
  };
  finalize(root);
  return root;
}

/** 这个节点（含其所有子目录）里的文档。按目录移除就是把这些文档 id 交给批量端点。 */
export function documentsUnder(node: DocumentTreeNode): DocumentSummary[] {
  return [...node.documents, ...node.directories.flatMap((child) => documentsUnder(child))];
}

export type SelectionState = "all" | "some" | "none";

/**
 * 勾选状态。`some` 是必需的：一个目录只被选了部分文档时，父目录的方框必须显示
 * "部分选中"，否则用户会以为整棵子树都会被移除。
 */
export function selectionState(
  node: DocumentTreeNode,
  selected: ReadonlySet<string>,
): SelectionState {
  const documents = documentsUnder(node);
  if (documents.length === 0) return "none";
  const chosen = documents.filter((document) => selected.has(document.id)).length;
  if (chosen === 0) return "none";
  return chosen === documents.length ? "all" : "some";
}

/** 顶层是否该显示成树：只要有一篇文档带路径，就说明 provider 给了层级。 */
export function hasDirectories(documents: DocumentSummary[]): boolean {
  return documents.some((document) => (document.path ?? []).length > 0);
}
