import { Play, RotateCcw, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { BatchProgressPayloadSchema } from "../../../../shared/contracts";
import type {
  BatchImport,
  EventEnvelope,
  BatchProgressPayload,
} from "../../../../shared/contracts";
import { appQueryClient } from "../../app/query-client";
import { clientErrorMessage } from "../settings/settings.queries";
import { batchKeys } from "./batch-import.queries";
import {
  useCancelBatchMutation,
  useContinueBatchMutation,
  useRetryBatchMutation,
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
  }, [batchId]);
  useEffect(() => {
    if (batch && terminal.has(batch.state))
      void appQueryClient.invalidateQueries({ queryKey: batchKeys.batch(batchId) });
  }, [batch, batchId]);
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
        {!isTerminal ? (
          <button
            aria-label="取消批量导入"
            disabled={cancelMutation.isPending}
            className="button button-secondary"
            onClick={() => void cancelMutation.mutateAsync(batchId)}
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
            onClick={() => void continueMutation.mutateAsync(batchId)}
            type="button"
          >
            <Play aria-hidden="true" size={15} />
            继续
          </button>
        ) : null}
        {batch.state === "failed" && batch.retryable ? (
          <button
            aria-label="重试批量导入"
            disabled={retryMutation.isPending}
            className="button button-secondary"
            onClick={() => void retryMutation.mutateAsync({ batchId })}
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
