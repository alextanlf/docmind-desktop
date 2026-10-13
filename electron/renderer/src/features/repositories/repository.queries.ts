import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export const repositoryKeys = {
  root: ["repositories"] as const,
  // 「已移除」单独一个键：它跟着 root 一起失效（前缀匹配），但读取与就地更新
  // 要能各走各的 —— 这些条目不是知识库，不能混进同一个列表。
  removed: ["repositories", "removed"] as const,
  documents: (repositoryId: string) => ["repositories", repositoryId, "documents"] as const,
  // 「已归档的文档」和文档列表分开：归档的文档不该出现在引用、范围或任何检索入口里，
  // 只该出现在设置页那份恢复/永久删除清单里。跨知识库，所以不带 repositoryId。
  archivedDocuments: ["documents", "archived"] as const,
  // 「已移除的远端文档」是旧流程留下的那批记录（文档行当时就删了，只能靠重新同步
  // 回来）。与归档分开存：两者的恢复方式不同 —— 归档是即时的，这批要重新下载。
  removedDocuments: ["documents", "removed"] as const,
  document: (documentId: string) => ["documents", documentId] as const,
};

export function useRepositoriesQuery() {
  return useQuery({
    queryKey: repositoryKeys.root,
    queryFn: () => window.docmind.repositories.list(),
  });
}

export function useDocumentsQuery(repositoryId: string | null) {
  return useQuery({
    queryKey: repositoryId ? repositoryKeys.documents(repositoryId) : ["repositories", "documents"],
    queryFn: () => window.docmind.documents.list(repositoryId!),
    enabled: repositoryId !== null,
  });
}

export function useDocumentQuery(documentId: string | null) {
  return useQuery({
    queryKey: documentId ? repositoryKeys.document(documentId) : ["documents", "detail"],
    queryFn: () => window.docmind.documents.read(documentId!),
    enabled: documentId !== null,
  });
}

export function useRepositorySyncQuery(repositoryId: string | null) {
  return useQuery({
    queryKey: ["repositories", repositoryId, "sync"],
    queryFn: () => window.docmind.sync.get(repositoryId!),
    enabled: repositoryId !== null,
  });
}

export function useUpdateRepositoryMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      repositoryId,
      remoteParentId,
    }: {
      repositoryId: string;
      remoteParentId: string | null;
    }) => window.docmind.repositories.update(repositoryId, { remoteParentId }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: repositoryKeys.root });
    },
  });
}

/**
 * 给知识库改名。
 *
 * 返回 `scope`，而不是只回一个仓库：`local_only` 表示远端名字没跟着变（provider
 * 改不了，或当前连接方式不支持），界面必须提示 —— 否则下一次列表刷新远端旧名字
 * 被拉回来时，用户会以为自己看错了。
 */
export function useRenameRepositoryMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ repositoryId, name }: { repositoryId: string; name: string }) =>
      window.docmind.repositories.rename(repositoryId, name),
    onSuccess: (result) => {
      queryClient.setQueryData<{ id: string }[]>(repositoryKeys.root, (current) =>
        current?.map((item) => (item.id === result.repository.id ? result.repository : item)),
      );
      void queryClient.invalidateQueries({ queryKey: repositoryKeys.root });
    },
  });
}

/**
 * 给文档改名（**只改标题**，不动正文）。
 *
 * 失效文档详情与它所在的列表，并把新详情直接写进缓存：改名后不该再看到旧标题挂着。
 * 不失效 `root` —— 知识库上的篇数没变，改名不会让它多一篇。
 */
export function useRenameDocumentMutation(repositoryId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ documentId, title }: { documentId: string; title: string }) =>
      window.docmind.documents.rename(documentId, title),
    onSuccess: (result) => {
      queryClient.setQueryData(repositoryKeys.document(result.document.id), result.document);
      void queryClient.invalidateQueries({ queryKey: repositoryKeys.documents(repositoryId) });
    },
  });
}

/**
 * 已从 DocMind 移除的远端知识库。
 *
 * 单独一条 query 而不是塞进 `repositories`：这些条目**不是**知识库 ——
 * 它们不该出现在范围选择、导入目标或记忆范围里。混进同一个列表，每个消费方
 * 都得自己记得过滤。
 */
export function useRemovedRepositoriesQuery() {
  return useQuery({
    queryKey: repositoryKeys.removed,
    queryFn: () => window.docmind.repositories.listRemoved(),
  });
}

/**
 * 移除一个知识库。
 *
 * 失效 root 就够了：它按前缀匹配，`removed` 与某个仓库的 `documents` 都在它
 * 下面。移除远端绑定的库会往「已移除」里加一条，所以那一份也必须跟着刷新。
 */
export function useRemoveRepositoryMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (repositoryId: string) => window.docmind.repositories.remove(repositoryId, true),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: repositoryKeys.root });
    },
  });
}

export function useRestoreRepositoryMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (removalId: string) => window.docmind.repositories.restore(removalId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: repositoryKeys.root });
    },
  });
}

export function useSyncRepositoryMutation(repositoryId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => window.docmind.sync.trigger(repositoryId!),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["repositories", repositoryId, "sync"] });
      void queryClient.invalidateQueries({ queryKey: ["repositories", repositoryId, "documents"] });
    },
  });
}

export function useConflictsQuery(repositoryId: string | null) {
  return useQuery({
    queryKey: ["repositories", repositoryId, "conflicts"],
    queryFn: () => window.docmind.conflicts.list(repositoryId!),
    enabled: repositoryId !== null,
  });
}

export function useResolveConflictMutation(repositoryId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      documentId: string;
      resolution: "keep_local" | "keep_remote" | "keep_both";
    }) => window.docmind.conflicts.resolve(input.documentId, input.resolution),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["repositories", repositoryId, "conflicts"],
      });
      void queryClient.invalidateQueries({
        queryKey: ["repositories", repositoryId, "documents"],
      });
    },
  });
}

export function useVersionsQuery(documentId: string | null) {
  return useQuery({
    queryKey: ["documents", documentId, "versions"],
    queryFn: () => window.docmind.versions.list(documentId!),
    enabled: documentId !== null,
  });
}

export function useGraphNodesQuery() {
  return useQuery({
    queryKey: ["graph", "nodes"],
    queryFn: () => window.docmind.graph.nodes(),
  });
}

export function useGraphAdjacencyQuery(nodeId: string | null) {
  return useQuery({
    queryKey: ["graph", "adjacency", nodeId],
    queryFn: () => window.docmind.graph.adjacency(nodeId!),
    enabled: nodeId !== null,
  });
}

/**
 * 已归档的文档（跨知识库，设置页那份列表）。
 *
 * 独立一条 query：归档的文档不是"文档"，不该出现在引用、范围或检索里，只服务于
 * 「恢复」与「永久删除」这两个动作。
 */
export function useArchivedDocumentsQuery() {
  return useQuery({
    queryKey: repositoryKeys.archivedDocuments,
    queryFn: () => window.docmind.documents.listArchived(),
  });
}

/**
 * 旧流程留下的「已移除、可重新同步」记录（跨知识库）。
 *
 * 与归档分开一条 query：归档的文档行还在，恢复是即时的；这一批的文档行当时就被删了，
 * 恢复要重新下载并重建索引。两者的文案与代价都不一样，混成一个列表就会骗人。
 */
export function useRemovedDocumentsQuery() {
  return useQuery({
    queryKey: repositoryKeys.removedDocuments,
    queryFn: () => window.docmind.documents.listRemoved(),
  });
}

/** 归档一处改动会影响的所有列表：可见文档、归档清单、知识库上的篇数统计。 */
function invalidateDocumentViews(
  queryClient: ReturnType<typeof useQueryClient>,
  repositoryId?: string,
) {
  void queryClient.invalidateQueries({ queryKey: repositoryKeys.archivedDocuments });
  void queryClient.invalidateQueries({ queryKey: repositoryKeys.root });
  if (repositoryId) {
    void queryClient.invalidateQueries({ queryKey: repositoryKeys.documents(repositoryId) });
  } else {
    // 单篇归档时手上只有文档 id，不知道它在哪个库；`documents` 这一层是
    // `["repositories", <id>, "documents"]`，所以按公共前缀整片失效。
    void queryClient.invalidateQueries({ queryKey: ["repositories"] });
  }
}

/**
 * 批量归档：把选中的文档从列表与检索里移出，数据全部保留。
 *
 * 与「永久删除」不同，它**不**需要打字确认 —— 恢复是即时的，误操作的代价是可撤销的。
 */
export function useArchiveDocumentsMutation(repositoryId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (documentIds: string[]) =>
      window.docmind.documents.archiveMany(repositoryId, documentIds),
    onSuccess: () => invalidateDocumentViews(queryClient, repositoryId),
  });
}

/** 归档单篇（文档详情页那个按钮）。 */
export function useArchiveDocumentMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (documentId: string) => window.docmind.documents.archive(documentId),
    onSuccess: () => invalidateDocumentViews(queryClient),
  });
}

/** 取消归档：数据一直都在，所以不需要重新下载，也不需要触发同步。 */
export function useUnarchiveDocumentMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (documentId: string) => window.docmind.documents.unarchive(documentId),
    onSuccess: () => {
      invalidateDocumentViews(queryClient);
      void queryClient.invalidateQueries({ queryKey: repositoryKeys.removedDocuments });
    },
  });
}

/**
 * 永久删除（只删本地）。远端原文由调用方决定要不要一起删 —— 那件事不可撤销，
 * 必须是一次显式的选择，而不是删除的默认含义。
 */
export function usePurgeDocumentMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ documentId, alsoRemote }: { documentId: string; alsoRemote: boolean }) =>
      alsoRemote
        ? window.docmind.documents.deleteRemote(documentId, true)
        : window.docmind.documents.remove(documentId, true),
    onSuccess: () => invalidateDocumentViews(queryClient),
  });
}

/**
 * 恢复一篇被移出的远端文档（旧流程）。
 *
 * 后端只负责"允许它回来"（删移出记录 + 清陈旧同步快照）；内容要重新下载并重建
 * 索引。所以调用方紧接着要触发一次同步 —— 少了那一步，用户看到的是"恢复点了
 * 没反应"。同步是**那个知识库**的，所以这里必须知道 repositoryId（设置页那份
 * 列表是跨库的，每一条各自不同）。
 */
export function useRestoreRemovedDocumentMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ repositoryId, removalId }: { repositoryId: string; removalId: string }) =>
      window.docmind.documents.restoreRemoved(repositoryId, removalId),
    onSuccess: (_result, variables) => {
      void queryClient.invalidateQueries({ queryKey: repositoryKeys.removedDocuments });
      invalidateDocumentViews(queryClient, variables.repositoryId);
    },
  });
}
