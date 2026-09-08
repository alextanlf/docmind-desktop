import { CheckCircle2, RotateCcw, Square, XCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { EventEnvelope, ImportJob } from "../../../../shared/contracts";
import { appQueryClient } from "../../app/query-client";
import { clientErrorMessage } from "../settings/settings.queries";
import { repositoryKeys } from "../repositories/repository.queries";
import { TaskProgress } from "../../components/TaskProgress";

const lastSequences = new Map<string, number>();
const terminalStates = new Set<ImportJob["state"]>(["completed", "failed", "cancelled"]);
type ImportProgressProps = { job: ImportJob; onOpenDocument?: (documentId: string) => void };
function patchJob(current: ImportJob, event: EventEnvelope): ImportJob {
  const payload = event.payload;
  return {
    ...current,
    progress: typeof payload.progress === "number" ? payload.progress : current.progress,
    currentStage:
      typeof payload.currentStage === "string" ? payload.currentStage : current.currentStage,
    message: typeof payload.message === "string" ? payload.message : current.message,
    state:
      typeof payload.state === "string" &&
      ["pending", "parsing", "uploading", "indexing", "completed", "failed", "cancelled"].includes(
        payload.state,
      )
        ? (payload.state as ImportJob["state"])
        : current.state,
    documentId: typeof payload.documentId === "string" ? payload.documentId : current.documentId,
    retryable: typeof payload.retryable === "boolean" ? payload.retryable : current.retryable,
    errorCode: typeof payload.code === "string" ? payload.code : current.errorCode,
    errorMessage:
      typeof payload.errorMessage === "string"
        ? payload.errorMessage
        : typeof payload.message === "string" && payload.state === "failed"
          ? payload.message
          : current.errorMessage,
  };
}
export function ImportProgress({ job, onOpenDocument }: ImportProgressProps) {
  const [activeJob, setActiveJob] = useState(job);
  const [error, setError] = useState("");
  const subscriptionRef = useRef<{ cancel: () => void } | null>(null);
  useEffect(() => {
    let active = true;
    async function initialize() {
      try {
        const current = await window.docmind.imports.get(job.id);
        if (!active) return;
        setActiveJob(current);
        const afterSequence = lastSequences.get(job.id) ?? 0;
        const subscription = window.docmind.imports.subscribe(job.id, afterSequence, (event) => {
          const last = lastSequences.get(job.id) ?? 0;
          if (event.sequence <= last) return;
          lastSequences.set(job.id, event.sequence);
          if (active) setActiveJob((currentJob) => patchJob(currentJob, event));
        });
        if (active) subscriptionRef.current = subscription;
        else subscription.cancel();
      } catch (cause) {
        if (active) setError(clientErrorMessage(cause));
      }
    }
    void initialize();
    return () => {
      active = false;
      subscriptionRef.current?.cancel();
      subscriptionRef.current = null;
    };
  }, [job.id]);
  useEffect(() => {
    if (!terminalStates.has(activeJob.state)) return;
    void appQueryClient.refetchQueries({ queryKey: repositoryKeys.root, type: "all" });
    if (activeJob.repositoryId) {
      void appQueryClient.refetchQueries({
        queryKey: repositoryKeys.documents(activeJob.repositoryId),
        type: "all",
      });
    }
  }, [activeJob.repositoryId, activeJob.state]);
  async function cancel() {
    try {
      setActiveJob(await window.docmind.imports.cancel(activeJob.id));
    } catch (cause) {
      setError(clientErrorMessage(cause));
    }
  }
  async function retry() {
    try {
      const retried = await window.docmind.imports.retry(activeJob.id);
      lastSequences.delete(activeJob.id);
      subscriptionRef.current?.cancel();
      subscriptionRef.current = null;
      setActiveJob(retried);
      const current = await window.docmind.imports.get(activeJob.id);
      setActiveJob(current);
      for (let attempt = 0; attempt < 50; attempt += 1) {
        if (terminalStates.has(current.state)) break;
        await new Promise((resolve) => setTimeout(resolve, 100));
        const next = await window.docmind.imports.get(activeJob.id);
        setActiveJob(next);
        if (terminalStates.has(next.state)) break;
      }
    } catch (cause) {
      setError(clientErrorMessage(cause));
    }
  }
  const isTerminal = terminalStates.has(activeJob.state);
  return (
    <section aria-label="导入进度" className="import-progress">
      <TaskProgress
        label="导入进度"
        progress={activeJob.progress}
        status={activeJob.currentStage ?? "导入任务"}
      />
      <p>{activeJob.message}</p>
      {activeJob.errorMessage ? (
        <p className="editor-error" role="alert">
          {activeJob.errorMessage}
        </p>
      ) : null}
      {error ? (
        <p className="editor-error" role="alert">
          {error}
        </p>
      ) : null}
      <div className="import-progress-actions">
        {!isTerminal ? (
          <button className="button button-secondary" onClick={() => void cancel()} type="button">
            <Square aria-hidden="true" size={15} />
            取消
          </button>
        ) : null}
        {activeJob.state === "failed" && activeJob.retryable ? (
          <button className="button button-secondary" onClick={() => void retry()} type="button">
            <RotateCcw aria-hidden="true" size={15} />
            重试导入
          </button>
        ) : null}
        {activeJob.state === "completed" && activeJob.documentId ? (
          <button
            className="button button-primary"
            onClick={() => onOpenDocument?.(activeJob.documentId!)}
            type="button"
          >
            <CheckCircle2 aria-hidden="true" size={15} />
            查看语雀文档
          </button>
        ) : null}
        {activeJob.state === "cancelled" ? (
          <span className="status-cancelled">
            <XCircle aria-hidden="true" size={15} />
            已取消
          </span>
        ) : null}
      </div>
    </section>
  );
}
