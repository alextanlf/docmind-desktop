import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export const repositoryKeys = {
  root: ["repositories"] as const,
  documents: (repositoryId: string) => ["repositories", repositoryId, "documents"] as const,
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
