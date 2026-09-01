import { X } from "lucide-react";
import { useState } from "react";
import type { ImportJob, SourceRef } from "../../../../shared/contracts";
import { Modal } from "../../components/Modal";
import { appQueryClient } from "../../app/query-client";
import { clientErrorMessage } from "../settings/settings.queries";
import { useEmbeddingStatusQuery } from "../settings/settings.queries";
import { repositoryKeys, useRepositoriesQuery } from "../repositories/repository.queries";
import { ConfirmStep } from "./ConfirmStep";
import { useImportStore } from "./import-store";
import { PreviewStep } from "./PreviewStep";
import { SourceStep } from "./SourceStep";

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
  const reset = useImportStore((state) => state.reset);
  const repositories = useRepositoriesQuery();
  const embedding = useEmbeddingStatusQuery();
  const [pending, setPending] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [error, setError] = useState("");
  if (!open) return null;
  function close() {
    reset();
    onClose();
  }
  async function inspect(currentSource: SourceRef) {
    const inspected = await window.docmind.imports.inspect(currentSource);
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
      setJobId(job.id);
      onImported?.(job);
    } catch (cause) {
      setError(clientErrorMessage(cause));
    } finally {
      setPending(false);
    }
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
        {step === 1 ? <SourceStep onContinue={inspect} /> : null}
        {step === 2 && preview ? (
          <PreviewStep
            duplicateDecision={duplicateDecision}
            onBack={() => setStep(1)}
            onContinue={() => setStep(3)}
            onDuplicateDecision={setDuplicateDecision}
            onRepositoryCreated={() =>
              void appQueryClient.invalidateQueries({ queryKey: repositoryKeys.root })
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
