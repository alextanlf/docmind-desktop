import { Download, LoaderCircle } from "lucide-react";
import type { ModelStatus, Repository, SourcePreview } from "../../../../shared/contracts";

type ConfirmStepProps = {
  preview: SourcePreview;
  repository: Repository;
  duplicateDecision: "skip" | "update" | null;
  embedding: ModelStatus | undefined;
  preparing: boolean;
  onPrepare: () => void;
  onBack: () => void;
  onConfirm: () => void;
  pending: boolean;
};
const operationLabel = (decision: "skip" | "update" | null) =>
  decision === "update" ? "更新" : decision === "skip" ? "跳过" : "创建";
export function ConfirmStep({
  preview,
  repository,
  duplicateDecision,
  embedding,
  preparing,
  onPrepare,
  onBack,
  onConfirm,
  pending,
}: ConfirmStepProps) {
  const ready = embedding?.state === "ready";
  return (
    <div className="import-step">
      <dl className="preview-details">
        <div>
          <dt>来源</dt>
          <dd>{preview.title}</dd>
        </div>
        <div>
          <dt>目标</dt>
          <dd>{repository.name}</dd>
        </div>
        <div>
          <dt>操作</dt>
          <dd>{operationLabel(duplicateDecision)}</dd>
        </div>
        <div>
          <dt>预期阶段</dt>
          <dd>解析、上传、索引</dd>
        </div>
      </dl>
      {!ready ? (
        <div className="embedding-gate">
          <div>
            <strong>准备 Embedding 模型</strong>
            <p>约 400 MB，首次导入前需要下载。</p>
            {embedding?.state === "error" ? (
              <p className="editor-error" role="alert">
                {embedding.message}
              </p>
            ) : null}
          </div>
          <button
            className="button button-secondary"
            disabled={preparing || embedding?.state === "downloading"}
            onClick={onPrepare}
            type="button"
          >
            {preparing || embedding?.state === "downloading" ? (
              <LoaderCircle aria-hidden="true" className="spin" size={16} />
            ) : (
              <Download aria-hidden="true" size={16} />
            )}
            {preparing || embedding?.state === "downloading" ? "正在准备…" : "准备模型"}
          </button>
        </div>
      ) : null}
      <div className="dialog-actions">
        <button
          className="button button-secondary"
          disabled={pending}
          onClick={onBack}
          type="button"
        >
          返回
        </button>
        <button
          className="button button-primary"
          disabled={!ready || pending}
          onClick={onConfirm}
          type="button"
        >
          {pending ? "正在创建…" : "确认导入"}
        </button>
      </div>
    </div>
  );
}
