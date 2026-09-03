import { useInfiniteQuery, useMutation, useQuery } from "@tanstack/react-query";
import type { ConfirmBatchInput, CreateBatchInput, RetryBatchInput } from "../../../../shared/contracts";

export const batchKeys = {
  all: ["batches"] as const,
  batch: (id: string) => ["batches", id] as const,
  items: (id: string) => ["batches", id, "items"] as const,
};

export function useBatchQuery(batchId: string | null) {
  return useQuery({ queryKey: batchId ? batchKeys.batch(batchId) : ["batches", "batch"], queryFn: () => window.docmind.batches.get(batchId!), enabled: !!batchId, refetchInterval: (query) => query.state.data?.state === "discovering" ? 1000 : false });
}

export function useBatchItemsQuery(batchId: string | null) {
  return useInfiniteQuery({
    queryKey: batchId ? batchKeys.items(batchId) : ["batches", "items"],
    queryFn: ({ pageParam }) => window.docmind.batches.listItems(batchId!, pageParam as string | null),
    enabled: !!batchId,
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.nextCursor ?? undefined,
  });
}

export function useCreateBatchMutation() {
  return useMutation({ mutationFn: (input: CreateBatchInput) => window.docmind.batches.create(input) });
}
export function useConfirmBatchMutation() {
  return useMutation({ mutationFn: ({ batchId, input }: { batchId: string; input: ConfirmBatchInput }) => window.docmind.batches.confirm(batchId, input) });
}
export function useCancelBatchMutation() {
  return useMutation({ mutationFn: (batchId: string) => window.docmind.batches.cancel(batchId) });
}
export function useContinueBatchMutation() {
  return useMutation({ mutationFn: (batchId: string) => window.docmind.batches.continue(batchId) });
}
export function useRetryBatchMutation() {
  return useMutation({ mutationFn: ({ batchId, input }: { batchId: string; input?: RetryBatchInput }) => window.docmind.batches.retry(batchId, input) });
}
