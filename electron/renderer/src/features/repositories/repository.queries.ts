import { useQuery } from "@tanstack/react-query";

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
