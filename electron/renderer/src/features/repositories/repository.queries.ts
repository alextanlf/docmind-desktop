import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export const repositoryKeys = {
  root: ["repositories"] as const,
  // 「已移除」单独一个键：它跟着 root 一起失效（前缀匹配），但读取与就地更新
  // 要能各走各的 —— 这些条目不是知识库，不能混进同一个列表。
  removed: ["repositories", "removed"] as const,
  documents: (repositoryId: string) => ["repositories", repositoryId, "documents"] as const,
  // 「已移除的文档」和文档列表分开：它们不该出现在引用、范围或任何检索入口里，
  // 只该出现在恢复入口里。
  removedDocuments: (repositoryId: string) =>
    ["repositories", repositoryId, "removedDocuments"] as const,
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
 * 已从 DocMind 移除的远端文档（远端原文还在）。
 *
 * 独立一条 query：这些文档不是"文档"，不该出现在引用、范围或检索里，只服务于
 * 「恢复」这一个动作。
 */
export function useRemovedDocumentsQuery(repositoryId: string | null) {
  return useQuery({
    queryKey: repositoryId
      ? repositoryKeys.removedDocuments(repositoryId)
      : ["documents", "removedDocuments"],
    queryFn: () => window.docmind.documents.listRemoved(repositoryId!),
    enabled: repositoryId !== null,
  });
}

/**
 * 批量把文档移出 DocMind（只删本地）。
 *
 * 失效三个键：文档列表（选中的要消失）、已移除列表（远端文档会多出条目）、
 * root（知识库上的篇数统计跟着变）。
 */
export function useRemoveDocumentsMutation(repositoryId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (documentIds: string[]) =>
      window.docmind.documents.removeMany(repositoryId, documentIds),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: repositoryKeys.root });
      void queryClient.invalidateQueries({
        queryKey: repositoryKeys.documents(repositoryId),
      });
      void queryClient.invalidateQueries({
        queryKey: repositoryKeys.removedDocuments(repositoryId),
      });
    },
  });
}

/**
 * 恢复一篇被移出的远端文档。
 *
 * 后端只负责"允许它回来"（删移出记录 + 清陈旧同步快照）；内容要重新下载并重建
 * 索引。所以调用方紧接着要触发一次同步 —— 少了那一步，用户看到的是"恢复点了
 * 没反应"。
 */
export function useRestoreRemovedDocumentMutation(repositoryId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (removalId: string) =>
      window.docmind.documents.restoreRemoved(repositoryId, removalId),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: repositoryKeys.removedDocuments(repositoryId),
      });
      void queryClient.invalidateQueries({
        queryKey: repositoryKeys.documents(repositoryId),
      });
      void queryClient.invalidateQueries({ queryKey: repositoryKeys.root });
    },
  });
}
