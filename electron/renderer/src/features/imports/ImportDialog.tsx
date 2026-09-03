import { X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { ImportJob, Repository, SourceRef } from "../../../../shared/contracts";
import { Modal } from "../../components/Modal";
import { appQueryClient } from "../../app/query-client";
import { clientErrorMessage } from "../settings/settings.queries";
import { useEmbeddingStatusQuery } from "../settings/settings.queries";
import { repositoryKeys, useRepositoriesQuery } from "../repositories/repository.queries";
import { ConfirmStep } from "./ConfirmStep";
import { useImportStore } from "./import-store";
import { PreviewStep } from "./PreviewStep";
import { SourceStep } from "./SourceStep";
import { BatchSourceStep } from "./BatchSourceStep";
import { BatchCandidateStep } from "./BatchCandidateStep";
import { useConfirmBatchMutation } from "./batch-import.queries";

type ImportDialogProps = {
  open: boolean;
  onClose: () => void;
  onImported?: (job: ImportJob) => void;
};
export function ImportDialog({ open, onClose, onImported }: ImportDialogProps) {
  const step = useImportStore((state) => state.step);
  const source = useImportStore((state) => state.source);
  const preview = useImportStore((state) => state.preview);
  const repositoryId = useImportStore((state) => state.repositoryId);
  const duplicateDecision = useImportStore((state) => state.duplicateDecision);
  const setStep = useImportStore((state) => state.setStep);
  const setSource = useImportStore((state) => state.setSource);
  const setPreview = useImportStore((state) => state.setPreview);
  const setRepositoryId = useImportStore((state) => state.setRepositoryId);
  const setDuplicateDecision = useImportStore((state) => state.setDuplicateDecision);
  const setJobId = useImportStore((state) => state.setJobId);
  const batchId = useImportStore((state) => state.batchId);
  const setBatchId = useImportStore((state) => state.setBatchId);
  const batchDecisions = useImportStore((state) => state.batchId ? state.batchDecisions[state.batchId] ?? {} : {});
  const batchItems = useImportStore((state) => state.batchId ? state.batchItems[state.batchId] ?? [] : []);
  const reset = useImportStore((state) => state.reset);
  const repositories = useRepositoriesQuery();
  const embedding = useEmbeddingStatusQuery();
  const [pending, setPending] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [error, setError] = useState("");
  const [mode, setMode] = useState<"single" | "batch">("single");
  const confirmBatchMutation = useConfirmBatchMutation();
  const [batchDiscoveryVersion, setBatchDiscoveryVersion] = useState(1);
  const inspectionGeneration = useRef(0);
  const openRef = useRef(open);
  openRef.current = open;
  useEffect(() => {
    if (!open) inspectionGeneration.current += 1;
  }, [open]);
  if (!open) return null;
  function close() {
    inspectionGeneration.current += 1;
    reset();
    setMode("single");
    onClose();
  }
  async function inspect(currentSource: SourceRef) {
    const generation = ++inspectionGeneration.current;
    const inspected = await window.docmind.imports.inspect(currentSource);
    if (!openRef.current || generation !== inspectionGeneration.current) return;
    setSource(currentSource);
    setPreview(inspected);
    setStep(2);
  }
  async function prepare() {
    setPreparing(true);
    setError("");
    try {
      const status = await window.docmind.embedding.prepare();
      await embedding.refetch();
      if (status.state === "ready") await embedding.refetch();
    } catch (cause) {
      setError(clientErrorMessage(cause));
    } finally {
      setPreparing(false);
    }
  }
  async function confirm() {
    if (!source || !preview || !repositoryId) return;
    setPending(true);
    setError("");
    try {
      const job = await window.docmind.imports.create({
        source,
        repositoryId,
        fingerprint: preview.fingerprint,
        duplicateDecision,
      });
      reset();
      setJobId(job.id);
      onImported?.(job);
    } catch (cause) {
      setError(clientErrorMessage(cause));
    } finally {
      setPending(false);
    }
  }
  async function confirmBatch() {
    if (!batchId) return;
    setPending(true); setError("");
    try {
      const current = await window.docmind.batches.get(batchId).catch(() => null);
      const items = batchItems.map((item) => {
        const local = batchDecisions[item.id];
        const decision = local ?? (item.selected && item.decision && item.allowedActions.includes(item.decision) ? item.decision : item.selected ? (item.allowedActions.find((a) => a !== "skip") ?? "skip") : "skip");
        return { itemId: item.id, decision };
      });
      await confirmBatchMutation.mutateAsync({ batchId, input: { discoveryVersion: current?.discoveryVersion ?? batchDiscoveryVersion, items } });
      setBatchId(batchId);
      onClose();
    } catch (cause) { setError(clientErrorMessage(cause)); }
    finally { setPending(false); }
  }
  const selectedRepository = repositories.data?.find(
    (repository) => repository.id === repositoryId,
  );
  return (
    <Modal className="import-dialog" labelledBy="import-dialog-title" onEscape={close}>
      <header className="import-dialog-header">
        <div>
          <h2 id="import-dialog-title">导入文档</h2>
          <ol aria-label="导入步骤" className="import-steps">
            <li className={step === 1 ? "is-active" : ""}>1 选择来源</li>
            <li className={step === 2 ? "is-active" : ""}>2 预览与目标</li>
            <li className={step === 3 ? "is-active" : ""}>3 确认导入</li>
          </ol>
          <div aria-label="导入模式" className="segmented-control mode-segmented-control" role="radiogroup">
            <label className={mode === "single" ? "is-active" : ""}><input type="radio" name="import-mode" checked={mode === "single"} onChange={() => { reset(); setBatchDiscoveryVersion(1); setMode("single"); }} />单篇</label>
            <label className={mode === "batch" ? "is-active" : ""}><input type="radio" name="import-mode" checked={mode === "batch"} onChange={() => { reset(); setBatchDiscoveryVersion(1); setMode("batch"); }} />批量</label>
          </div>
        </div>
        <button
          aria-label="关闭导入窗口"
          className="tree-icon-button"
          onClick={close}
          title="关闭导入窗口"
          type="button"
        >
          <X aria-hidden="true" size={17} />
        </button>
      </header>
      <div className="import-dialog-content">
        {step === 1 && mode === "single" ? <SourceStep onContinue={inspect} /> : null}
        {step === 1 && mode === "batch" ? <BatchSourceStep repositories={repositories.data ?? []} onCreated={(id, version) => { setBatchId(id); setBatchDiscoveryVersion(version); setStep(2); }} /> : null}
        {step === 2 && mode === "batch" && batchId ? <BatchCandidateStep batchId={batchId} onConfirm={() => void confirmBatch()} onCancel={close} pending={pending} /> : null}
        {step === 2 && preview ? (
          <PreviewStep
            duplicateDecision={duplicateDecision}
            onBack={() => setStep(1)}
            onContinue={() => setStep(3)}
            onDuplicateDecision={setDuplicateDecision}
            onRepositoryCreated={(repository) =>
              appQueryClient.setQueryData<Repository[]>(repositoryKeys.root, (current = []) => [
                ...current.filter((item) => item.id !== repository.id),
                repository,
              ])
            }
            onSelectRepository={setRepositoryId}
            preview={preview}
            repositories={repositories.data ?? []}
            selectedRepositoryId={repositoryId}
          />
        ) : null}
        {step === 3 && preview && selectedRepository ? (
          <ConfirmStep
            duplicateDecision={duplicateDecision}
            embedding={embedding.data}
            onBack={() => setStep(2)}
            onConfirm={() => void confirm()}
            onPrepare={() => void prepare()}
            pending={pending}
            preparing={preparing}
            preview={preview}
            repository={selectedRepository}
          />
        ) : null}
        {error ? (
          <p className="editor-error" role="alert">
            {error}
          </p>
        ) : null}
      </div>
    </Modal>
  );
}
