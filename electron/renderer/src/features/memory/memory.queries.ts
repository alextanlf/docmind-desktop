import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  DistillationEdit,
  DistillationTarget,
  MemoryListInput,
} from "../../../../shared/contracts";

export const memoryKeys = {
  all: ["memory"] as const,
  list: (input: MemoryListInput) => ["memory", "list", input] as const,
  summary: (sessionId: string) => ["memory", "summary", sessionId] as const,
  distillation: (id: string) => ["memory", "distillation", id] as const,
};
export function useMemoryListQuery(input: MemoryListInput | null) {
  return useQuery({
    queryKey: input ? memoryKeys.list(input) : memoryKeys.all,
    queryFn: () => window.docmind.memory.list(input!),
    enabled: input !== null,
  });
}
export function useSummaryQuery(sessionId: string | null) {
  return useQuery({
    queryKey: sessionId ? memoryKeys.summary(sessionId) : memoryKeys.all,
    queryFn: () => window.docmind.memory.getSummary(sessionId!),
    enabled: sessionId !== null,
  });
}
export function useDistillationQuery(id: string | null) {
  return useQuery({
    queryKey: id ? memoryKeys.distillation(id) : memoryKeys.all,
    queryFn: () => window.docmind.memory.getDistillation(id!),
    enabled: id !== null,
  });
}
export function useDistillationMutations(id: string) {
  const client = useQueryClient();
  const refresh = () => client.invalidateQueries({ queryKey: memoryKeys.distillation(id) });
  return {
    update: useMutation({
      mutationFn: (input: DistillationEdit) => window.docmind.memory.updateDistillation(id, input),
      onSuccess: refresh,
    }),
    regenerate: useMutation({
      mutationFn: () => window.docmind.memory.regenerateDistillation(id),
      onSuccess: refresh,
    }),
    save: useMutation({
      mutationFn: (input: DistillationTarget) => window.docmind.memory.saveDistillation(id, input),
      onSuccess: async () => {
        await refresh();
        await client.invalidateQueries({ queryKey: memoryKeys.all });
      },
    }),
  };
}
export function useSummaryMutations(sessionId: string) {
  const client = useQueryClient();
  const refresh = () => client.invalidateQueries({ queryKey: memoryKeys.summary(sessionId) });
  return {
    regenerate: useMutation({
      mutationFn: () => window.docmind.memory.regenerateSummary(sessionId),
      onSuccess: refresh,
    }),
    remove: useMutation({
      mutationFn: () => window.docmind.memory.deleteSummary(sessionId),
      onSuccess: async () => {
        await refresh();
        await client.invalidateQueries({ queryKey: memoryKeys.all });
      },
    }),
  };
}
