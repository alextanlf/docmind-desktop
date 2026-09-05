import { Play, RotateCcw, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { BatchProgressPayloadSchema } from "../../../../shared/contracts";
import type {
  BatchImport,
  EventEnvelope,
  BatchProgressPayload,
} from "../../../../shared/contracts";
import { clientErrorMessage } from "../settings/settings.queries";
import { batchKeys } from "./batch-import.queries";
import {
  useCancelBatchMutation,
  useContinueBatchMutation,
  useRetryBatchMutation,
  useBatchItemsQuery,
} from "./batch-import.queries";

const lastSequences = new Map<string, number>();
const terminal = new Set<BatchImport["state"]>([
  "completed",
  "completed_with_errors",
  "failed",
  "cancelled",
]);
function patchBatch(current: BatchImport, event: EventEnvelope): BatchImport {
  const parsed = BatchProgressPayloadSchema.safeParse(event.payload);
  if (!parsed.success) return current;
  const p: BatchProgressPayload = parsed.data;
  const counts = p.counts;
  return {
    ...current,
    progress: typeof p.progress === "number" ? p.progress : current.progress,
    message: typeof p.message === "string" ? p.message : current.message,
    state:
      typeof p.state === "string" &&
      [
        "discovering",
        "awaiting_confirmation",
        "running",
        "paused",
        "completed",
        "completed_with_errors",
        "failed",
        "cancelled",
      ].includes(p.state)
        ? (p.state as BatchImport["state"])
        : current.state,
    completedCount:
      counts && typeof counts.completed === "number" ? counts.completed : current.completedCount,
    failedCount: counts && typeof counts.failed === "number" ? counts.failed : current.failedCount,
    skippedCount:
      counts && typeof counts.skipped === "number" ? counts.skipped : current.skippedCount,
    totalCount: counts && typeof counts.total === "number" ? counts.total : current.totalCount,
    selectedCount:
      counts && typeof counts.selected === "number" ? counts.selected : current.selectedCount,
  };
}
export function BatchProgress({ batchId }: { batchId: string }) {
  const [batch, setBatch] = useState<BatchImport | null>(null);
  const [error, setError] = useState("");
  const [replayAttempt, setReplayAttempt] = useState(0);
  const client = useQueryClient();
  const items = useBatchItemsQuery(batch?.state === "completed_with_errors" ? batchId : null);
  const { hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage } = items;
  useEffect(() => {
    if (hasNextPage && !isFetchingNextPage && !isFetchNextPageError) void fetchNextPage();
  }, [hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage]);
  const retryItemIds = items.data?.pages.flatMap((page) => page.items)
    .filter((item) => item.state === "failed" && item.retryable && item.importJobId)
    .map((item) => item.id) ?? [];
  const cancelMutation = useCancelBatchMutation();
  const continueMutation = useContinueBatchMutation();
  const retryMutation = useRetryBatchMutation();
  const sub = useRef<{ cancel: () => void } | null>(null);
  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const current = await window.docmind.batches.get(batchId);
        if (!active) return;
        setBatch(current);
        const after = current.lastEventSequence ?? lastSequences.get(batchId) ?? 0;
        lastSequences.set(batchId, after);
        const subscription = window.docmind.batches.subscribe(batchId, after, (event) => {
          const last = lastSequences.get(batchId) ?? after;
          if (event.sequence <= last) return;
          lastSequences.set(batchId, event.sequence);
          if (active) setBatch((value) => (value ? patchBatch(value, event) : value));
        });
        if (active) sub.current = subscription;
        else subscription.cancel();
      } catch (cause) {
        if (active) setError(clientErrorMessage(cause));
      }
    })();
    return () => {
      active = false;
      sub.current?.cancel();
      sub.current = null;
    };
  }, [batchId, replayAttempt]);
  useEffect(() => {
    if (batch && terminal.has(batch.state))
      void client.invalidateQueries({ queryKey: batchKeys.batch(batchId) });
  }, [batch, batchId, client]);
  const resumeProgress = (current: BatchImport) => {
    setBatch(current);
    void client.invalidateQueries({ queryKey: batchKeys.items(batchId) });
    setReplayAttempt((attempt) => attempt + 1);
  };
  if (!batch)
    return error ? (
      <p className="editor-error" role="alert">
        {error}
      </p>
    ) : (
      <p>正在加载批量导入…</p>
    );
  const isTerminal = terminal.has(batch.state);
  return (
    <section aria-label="批量导入进度" className="import-progress">
      <div className="import-progress-title">
        <strong>{batch.message}</strong>
        <span>{batch.progress}%</span>
      </div>
      <progress aria-label="批量导入进度" aria-valuenow={batch.progress} max={100} value={batch.progress} />{" "}
      <p>
        {batch.completedCount}/{batch.selectedCount} 已完成，{batch.failedCount} 失败，
        {batch.skippedCount} 跳过
      </p>
      {batch.errorMessage ? (
        <p className="editor-error" role="alert">
          {batch.errorMessage}
        </p>
      ) : null}
      {[cancelMutation.error, continueMutation.error, retryMutation.error].some(Boolean) ? (
        <p className="editor-error" role="alert">
          操作失败，请重试
        </p>
      ) : null}
      <div className="import-progress-actions">
        {batch.state === "completed_with_errors" && items.isError ? (
          <div>
            <p className="editor-error" role="alert">批次项加载失败，请重试加载后再重试导入</p>
            <button
              aria-label="重试加载批次项"
              className="button button-secondary"
              disabled={items.isFetching}
              onClick={() => { void (isFetchNextPageError ? fetchNextPage() : items.refetch()); }}
              type="button"
            >
              重试加载
            </button>
          </div>
        ) : null}
        {!isTerminal ? (
          <button
            aria-label="取消批量导入"
            disabled={cancelMutation.isPending}
            className="button button-secondary"
            onClick={() => cancelMutation.mutate(batchId, { onSuccess: setBatch })}
            type="button"
          >
            <Square aria-hidden="true" size={15} />
            取消
          </button>
        ) : null}
        {batch.state === "paused" ? (
          <button
            aria-label="继续批量导入"
            disabled={continueMutation.isPending}
            className="button button-secondary"
            onClick={() => continueMutation.mutate(batchId, { onSuccess: resumeProgress })}
            type="button"
          >
            <Play aria-hidden="true" size={15} />
            继续
          </button>
        ) : null}
        {batch.state === "completed_with_errors" && retryItemIds.length > 0 ? (
          <button
            aria-label="重试批量导入"
            disabled={retryMutation.isPending || items.isFetching || items.isError || hasNextPage}
            className="button button-secondary"
            onClick={() => retryMutation.mutate({ batchId, input: { itemIds: retryItemIds } }, { onSuccess: resumeProgress })}
            type="button"
          >
            <RotateCcw aria-hidden="true" size={15} />
            重试
          </button>
        ) : null}
      </div>
    </section>
  );
}
