import type { ModelStatus, Repository, SourcePreview } from "../../../../shared/contracts";

type ConfirmStepProps = {
  preview: SourcePreview;
  repository: Repository;
  duplicateDecision: "skip" | "update" | null;
  embedding: ModelStatus | undefined;
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
  onBack,
  onConfirm,
  pending,
}: ConfirmStepProps) {
  // 嵌入模型由后端在应用启动时自动预热（backend/app/main.py lifespan），
  // 索引入口还有require_ready_embedding() 兜底，所以这里**不**设置任何门禁：
  // 唯一值得拦住的是「加载已经失败」，那种情况点了确认也只会拿到 503。
  const broken = embedding?.state === "error";
  const warming = embedding?.state === "unavailable" || embedding?.state === "downloading";
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
          <dd>{repository.provider ? "解析、上传、索引" : "解析、索引"}</dd>
        </div>
      </dl>
      {broken ? (
        <p className="editor-error" role="alert">
          {embedding?.message}
        </p>
      ) : warming ? (
        // 预热通常在用户走到这一步之前就完成了，所以只做一句轻量提示、不阻断操作。
        <p className="muted-row">向量模型正在就绪，索引时会自动等待。</p>
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
          disabled={pending || broken}
          onClick={onConfirm}
          type="button"
        >
          {pending ? "正在创建…" : "确认导入"}
        </button>
      </div>
    </div>
  );
}