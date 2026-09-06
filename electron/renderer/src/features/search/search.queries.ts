import { useMutation, useQuery } from "@tanstack/react-query";
import type { SearchImportInput } from "../../../../shared/contracts";

export function useSearchRunQuery(runId: string | null, sessionId: string | null) {
  return useQuery({
    queryKey: ["web-search", runId, sessionId],
    queryFn: () => window.docmind.webSearch.getRun(runId!, sessionId!),
    enabled: Boolean(runId && sessionId),
  });
}
export function useCreateSearchImportMutation(runId: string) {
  return useMutation({
    mutationFn: (input: SearchImportInput) =>
      window.docmind.webSearch.createImportBatch(runId, input),
  });
}
