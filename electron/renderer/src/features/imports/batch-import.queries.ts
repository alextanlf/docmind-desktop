import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  ConfirmBatchInput,
  CreateBatchInput,
  RetryBatchInput,
} from "../../../../shared/contracts";
import { appQueryClient } from "../../app/query-client";

export const batchKeys = {
  all: ["batches"] as const,
  batch: (id: string) => ["batches", id] as const,
  items: (id: string) => ["batches", id, "items"] as const,
  diagnostics: (id: string) => ["batches", id, "diagnostics"] as const,
};

/**
 * 哪些页面没被收录、各自为什么。
 *
 * 单独一次请求而不是塞进批次快照：一个 200 页的站点被拒几十上百条是常态，而候选页
 * 每加载一次都要读批次 —— 挂在那个响应里，等于每次搬运一份用户大多数时候不看的
 * 数据。`staleTime` 给得长：抓取结论在确认阶段不会再变（要变也是用户重新发起导入）。
 */
export function useBatchDiagnosticsQuery(batchId: string | null) {
  return useQuery({
    queryKey: batchId ? batchKeys.diagnostics(batchId) : ["batches", "diagnostics"],
    queryFn: () => window.docmind.batches.diagnostics(batchId!),
    enabled: !!batchId,
    staleTime: 5 * 60 * 1000,
  });
}

export function useBatchQuery(batchId: string | null) {
  return useQuery({
    queryKey: batchId ? batchKeys.batch(batchId) : ["batches", "batch"],
    queryFn: () => window.docmind.batches.get(batchId!),
    enabled: !!batchId,
    refetchInterval: (query) => (query.state.data?.state === "discovering" ? 1000 : false),
  });
}

export function useBatchItemsQuery(batchId: string | null) {
  return useInfiniteQuery({
    queryKey: batchId ? batchKeys.items(batchId) : ["batches", "items"],
    queryFn: ({ pageParam }) =>
      window.docmind.batches.listItems(batchId!, pageParam as string | null),
    enabled: !!batchId,
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.nextCursor ?? undefined,
  });
}

export function useCreateBatchMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateBatchInput) => window.docmind.batches.create(input),
    onSuccess: (batch) => {
      if (batch) client.setQueryData(batchKeys.batch(batch.id), batch);
    },
  });
}
export function useConfirmBatchMutation() {
  return useMutation({
    mutationFn: ({ batchId, input }: { batchId: string; input: ConfirmBatchInput }) =>
      window.docmind.batches.confirm(batchId, input),
    onSuccess: (batch) => {
      if (batch) appQueryClient.setQueryData(batchKeys.batch(batch.id), batch);
    },
  });
}
export function useCancelBatchMutation() {
  return useMutation({
    mutationFn: (batchId: string) => window.docmind.batches.cancel(batchId),
    onSuccess: (batch) => {
      if (batch) appQueryClient.setQueryData(batchKeys.batch(batch.id), batch);
    },
  });
}
export function useContinueBatchMutation() {
  return useMutation({
    mutationFn: (batchId: string) => window.docmind.batches.continue(batchId),
    onSuccess: (batch) => {
      if (batch) appQueryClient.setQueryData(batchKeys.batch(batch.id), batch);
    },
  });
}
export function useRetryBatchMutation() {
  return useMutation({
    mutationFn: ({ batchId, input }: { batchId: string; input?: RetryBatchInput }) =>
      window.docmind.batches.retry(batchId, input),
    onSuccess: (batch) => {
      if (batch) appQueryClient.setQueryData(batchKeys.batch(batch.id), batch);
    },
  });
}
