import { Eye, Pencil, Save, Trash2, X } from "lucide-react";
import { Suspense, lazy, useState } from "react";
import type { DocumentDetail } from "../../../../shared/contracts";
import { appQueryClient } from "../../app/query-client";
import { MarkdownBody } from "../../components/MarkdownBody";
import { clientErrorMessage } from "../settings/settings.queries";
import { DeleteDocumentDialog } from "./DeleteDocumentDialog";
import { repositoryKeys } from "./repository.queries";

/**
 * pdf.js 是 1.7MB 的独立依赖，绝大多数会话用不到。
 * 按需加载，避免每次启动都要解析它。
 */
const PdfOriginalView = lazy(() =>
  import("./PdfOriginalView").then((module) => ({ default: module.PdfOriginalView })),
);

type DocumentEditorProps = {
  document: DocumentDetail;
  repositoryName?: string | null;
  onClose: () => void;
};

type ViewMode = "read" | "edit";
/** 阅读视图下看什么：解析后的正文，还是导入时的原件。 */
type SourceMode = "parsed" | "original";

export function DocumentEditor({
  document,
  repositoryName = "知识库",
  onClose,
}: DocumentEditorProps) {
  // 默认阅读模式：默认展示渲染后的正文，不直接抛原始 Markdown 源码。
  const [mode, setMode] = useState<ViewMode>("read");
  // 有 PDF 原件时默认看原文 —— PDF 转 Markdown 会丢掉版式，
  // 阅读体验只有原件能给全（解析视图仍在旁边一键可达）。
  const hasOriginal =
    document.originalMediaType === "application/pdf" && (document.originalByteSize ?? 0) > 0;
  const [source, setSource] = useState<SourceMode>(hasOriginal ? "original" : "parsed");
  const [title, setTitle] = useState(document.title);
  const [content, setContent] = useState(document.content);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState("");

  const dirty = title.trim() !== document.title || content !== document.content;

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
      setMode("read");
    } catch (cause) {
      setError(clientErrorMessage(cause));
    } finally {
      setSaving(false);
    }
  }

  function switchMode(next: ViewMode) {
    if (next === mode) return;
    if (next === "read" && dirty) {
      // 有未保存改动时先回到已保存内容，避免阅读视图与库内文档不一致。
      setTitle(document.title);
      setContent(document.content);
    }
    // 编辑的对象是解析后的 Markdown，原文视图下不允许直接进编辑。
    if (next === "edit") setSource("parsed");
    setError("");
    setMode(next);
  }

  return (
    <section aria-label="文档编辑器" className="document-editor">
      <header className="document-editor-header">
        <div>
          {mode === "edit" ? (
            <input
              aria-label="文档标题"
              onChange={(event) => setTitle(event.target.value)}
              value={title}
            />
          ) : (
            <h1 className="document-view-title">{title}</h1>
          )}
        </div>
        {hasOriginal && mode === "read" ? (
          <div aria-label="内容来源" className="document-source-toggle" role="group">
            <button
              aria-pressed={source === "original"}
              onClick={() => setSource("original")}
              type="button"
            >
              原文
            </button>
            <button
              aria-pressed={source === "parsed"}
              onClick={() => setSource("parsed")}
              type="button"
            >
              解析
            </button>
          </div>
        ) : null}
        <div className="editor-actions">
          {mode === "read" ? (
            <button
              aria-label="编辑文档"
              className="tree-icon-button"
              onClick={() => switchMode("edit")}
              title="编辑文档"
              type="button"
            >
              <Pencil aria-hidden="true" size={16} />
            </button>
          ) : (
            <button
              aria-label="预览文档"
              className="tree-icon-button"
              onClick={() => switchMode("read")}
              title="预览文档"
              type="button"
            >
              <Eye aria-hidden="true" size={16} />
            </button>
          )}
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
      {mode === "read" ? (
        hasOriginal && source === "original" ? (
          <Suspense
            fallback={
              <div className="pdf-original" role="status">
                <p className="pdf-original-loading">正在加载阅读器…</p>
              </div>
            }
          >
            <PdfOriginalView byteSize={document.originalByteSize ?? 0} documentId={document.id} />
          </Suspense>
        ) : (
          <article aria-label="文档内容" className="document-view">
            {content.trim() ? (
              <MarkdownBody content={content} />
            ) : (
              <p className="document-view-empty">这篇文档还没有内容。</p>
            )}
          </article>
        )
      ) : (
        <textarea
          aria-label="Markdown 内容"
          className="markdown-editor"
          onChange={(event) => setContent(event.target.value)}
          value={content}
        />
      )}
      {error ? (
        <p className="editor-error" role="alert">
          {error}
        </p>
      ) : null}
      {mode === "edit" ? (
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
      ) : null}
      {deleting ? (
        <DeleteDocumentDialog
          document={document}
          onClose={() => setDeleting(false)}
          onDeleted={onClose}
          repositoryName={repositoryName ?? "知识库"}
        />
      ) : null}
    </section>
  );
}
