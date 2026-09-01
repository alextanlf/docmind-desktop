import { Save, Trash2, X } from "lucide-react";
import { useState } from "react";
import type { DocumentDetail } from "../../../../shared/contracts";
import { appQueryClient } from "../../app/query-client";
import { clientErrorMessage } from "../settings/settings.queries";
import { DeleteDocumentDialog } from "./DeleteDocumentDialog";
import { repositoryKeys } from "./repository.queries";

type DocumentEditorProps = {
  document: DocumentDetail;
  repositoryName?: string;
  onClose: () => void;
};

export function DocumentEditor({
  document,
  repositoryName = "知识库",
  onClose,
}: DocumentEditorProps) {
  const [title, setTitle] = useState(document.title);
  const [content, setContent] = useState(document.content);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState("");

  async function save() {
    if (!title.trim() || !content.trim()) return;
    setSaving(true);
    setError("");
    try {
      const updated = await window.docmind.documents.update(document.id, {
        title: title.trim(),
        content,
      });
      appQueryClient.setQueryData(repositoryKeys.document(document.id), updated);
      await appQueryClient.invalidateQueries({
        queryKey: repositoryKeys.documents(document.repositoryId),
      });
      await appQueryClient.invalidateQueries({ queryKey: repositoryKeys.root });
    } catch (cause) {
      setError(clientErrorMessage(cause));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section aria-label="文档编辑器" className="document-editor">
      <header className="document-editor-header">
        <div>
          <input
            aria-label="文档标题"
            onChange={(event) => setTitle(event.target.value)}
            value={title}
          />
        </div>
        <div className="editor-actions">
          <button
            aria-label="删除文档"
            className="tree-icon-button"
            onClick={() => setDeleting(true)}
            title="删除文档"
            type="button"
          >
            <Trash2 aria-hidden="true" size={16} />
          </button>
          <button
            aria-label="关闭文档"
            className="tree-icon-button"
            onClick={onClose}
            title="关闭文档"
            type="button"
          >
            <X aria-hidden="true" size={16} />
          </button>
        </div>
      </header>
      <textarea
        aria-label="Markdown 内容"
        className="markdown-editor"
        onChange={(event) => setContent(event.target.value)}
        value={content}
      />
      {error ? (
        <p className="editor-error" role="alert">
          {error}
        </p>
      ) : null}
      <footer className="document-editor-footer">
        <button className="button button-secondary" onClick={onClose} type="button">
          取消
        </button>
        <button
          className="button button-primary"
          disabled={saving || !title.trim() || !content.trim()}
          onClick={() => void save()}
          type="button"
        >
          <Save aria-hidden="true" size={16} />
          {saving ? "保存中…" : "保存文档"}
        </button>
      </footer>
      {deleting ? (
        <DeleteDocumentDialog
          document={document}
          onClose={() => setDeleting(false)}
          onDeleted={onClose}
          repositoryName={repositoryName}
        />
      ) : null}
    </section>
  );
}
