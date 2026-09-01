import { useState } from "react";
import type { Repository, SourcePreview } from "../../../../shared/contracts";
import { clientErrorMessage } from "../settings/settings.queries";

type PreviewStepProps = {
  preview: SourcePreview;
  repositories: Repository[];
  selectedRepositoryId: string | null;
  duplicateDecision: "skip" | "update" | null;
  onSelectRepository: (repositoryId: string) => void;
  onDuplicateDecision: (value: "skip" | "update" | null) => void;
  onRepositoryCreated: (repository: Repository) => void;
  onBack: () => void;
  onContinue: () => void;
};
export function PreviewStep({
  preview,
  repositories,
  selectedRepositoryId,
  duplicateDecision,
  onSelectRepository,
  onDuplicateDecision,
  onRepositoryCreated,
  onBack,
  onContinue,
}: PreviewStepProps) {
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  async function createRepository() {
    const name = newName.trim();
    if (!name) return;
    setCreating(true);
    setError("");
    try {
      const repository = await window.docmind.repositories.create({ name });
      onRepositoryCreated(repository);
      onSelectRepository(repository.id);
      setNewName("");
    } catch (cause) {
      setError(clientErrorMessage(cause));
    } finally {
      setCreating(false);
    }
  }
  return (
    <div className="import-step">
      <dl className="preview-details">
        <div>
          <dt>标题</dt>
          <dd>{preview.title}</dd>
        </div>
        <div>
          <dt>类型</dt>
          <dd>{preview.mediaType}</dd>
        </div>
        <div>
          <dt>大小</dt>
          <dd>{Math.max(1, Math.ceil(preview.sizeBytes / 1024))} KB</dd>
        </div>
        <div>
          <dt>{preview.sourceKind === "url" ? "来源 URL" : "文件名"}</dt>
          <dd>{preview.sourceUrl ?? preview.title}</dd>
        </div>
      </dl>
      {preview.warnings.length > 0 ? (
        <div className="preview-warnings" role="alert">
          {preview.warnings.map((warning) => (
            <p key={warning}>{warning}</p>
          ))}
        </div>
      ) : null}
      <label className="dialog-field">
        <span>目标知识库</span>
        <select
          aria-label="目标知识库"
          onChange={(event) => onSelectRepository(event.target.value)}
          value={selectedRepositoryId ?? ""}
        >
          <option value="">请选择知识库</option>
          {repositories.map((repository) => (
            <option key={repository.id} value={repository.id}>
              {repository.name}
            </option>
          ))}
        </select>
      </label>
      <div className="inline-create">
        <label className="dialog-field">
          <span>新建知识库</span>
          <input
            aria-label="新建知识库名称"
            onChange={(event) => setNewName(event.target.value)}
            value={newName}
          />
        </label>
        <button
          className="button button-secondary"
          disabled={creating || !newName.trim()}
          onClick={() => void createRepository()}
          type="button"
        >
          {creating ? "创建中…" : "新建知识库"}
        </button>
      </div>
      <label className="dialog-field">
        <span>重复内容处理</span>
        <select
          aria-label="重复内容处理"
          onChange={(event) =>
            onDuplicateDecision(
              event.target.value ? (event.target.value as "skip" | "update") : null,
            )
          }
          value={duplicateDecision ?? ""}
        >
          <option value="">创建</option>
          <option value="update">更新已有文档</option>
          <option value="skip">跳过重复内容</option>
        </select>
      </label>
      {error ? (
        <p className="editor-error" role="alert">
          {error}
        </p>
      ) : null}
      <div className="dialog-actions">
        <button className="button button-secondary" onClick={onBack} type="button">
          返回
        </button>
        <button
          className="button button-primary"
          disabled={!selectedRepositoryId}
          onClick={onContinue}
          type="button"
        >
          继续
        </button>
      </div>
    </div>
  );
}
