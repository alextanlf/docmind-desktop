import {
  Eye,
  FileWarning,
  Pencil,
  RefreshCw,
  Save,
  SquareArrowOutUpRight,
  Trash2,
  X,
} from "lucide-react";
import { Suspense, lazy, useEffect, useState } from "react";
import { ANY_FORMAT, type DocumentDetail } from "../../../../shared/contracts";
import { appQueryClient } from "../../app/query-client";
import { MarkdownBody } from "../../components/MarkdownBody";
import { DocumentFigures } from "./DocumentFigures";
import { clientErrorMessage } from "../settings/settings.queries";
import { DeleteDocumentDialog } from "./DeleteDocumentDialog";
import { ReconvertOriginalDialog } from "./ReconvertOriginalDialog";
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
  const [confirmingReconvert, setConfirmingReconvert] = useState(false);
  const [error, setError] = useState("");
  /** 重新定位/重新转换进行中：两者都会走网络与磁盘，按钮要能挡住重复点击。 */
  const [busy, setBusy] = useState(false);
  /**
   * 从解析视图点图之后要定位的原件位置。
   *
   * 只在这次交互里有效：用户一旦自己滚走，就不该再被拽回去。切换文档时清掉（见下面的
   * `useEffect`），否则会给新文档传一个属于旧文档的页码。
   */
  const [locate, setLocate] = useState<{
    page: number;
    bbox: [number, number, number, number] | null;
  } | null>(null);

  /**
   * 原件状态的提示语。
   *
   * `missing` 与 `none` 必须分开说：前者是"文件被挪走了"（用户能修，给他重新定位），
   * 后者是"这篇本来就没有原件"（不需要任何操作，也不该出现提示）。
   * 文案住在前端，后端只送状态码 —— 与全仓库同一条约定。
   */
  const originalNotice =
    document.originalState === "changed"
      ? "磁盘上的原件已经变过，当前正文是上一次转换的结果。"
      : document.originalState === "missing"
        ? "找不到原件了 —— 它可能被移动、改名或删除。正文仍然可用。"
        : "";

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
      applyUpdated(updated);
      setMode("read");
    } catch (cause) {
      setError(clientErrorMessage(cause));
    } finally {
      setSaving(false);
    }
  }

  /**
   * 用系统程序打开原件（Word、Preview……）。
   *
   * 这条路是"以原格式修改"的正式做法：应用没有 docx/pdf 的写入器，也不该有 ——
   * 让 Word 去改 Word，改完回来点「重新转换」。这里只发 documentId，
   * 路径由主进程向后端要、验签之后才交给系统程序。
   */
  async function openOriginal() {
    setBusy(true);
    setError("");
    try {
      await window.docmind.documents.openOriginal(document.id);
    } catch (cause) {
      setError(clientErrorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  /**
   * 重新转换：重新读原件、重新解析。
   *
   * 它复用后端的"编辑保存"流水线，所以视图刷新与保存完全一致 —— 保存后要失效的那几个
   * 查询在这里也一样要失效，否则树上的标题/字数会留着旧值。
   *
   * 入口一律先过确认弹窗：它整篇替换正文，而"原件已更新"的提示本身就在鼓励用户去原件里
   * 改 —— 那条路最容易顺手抹掉应用内改过的东西。
   */
  async function reconvert() {
    setBusy(true);
    setError("");
    try {
      const updated = await window.docmind.documents.reconvert(document.id);
      applyUpdated(updated);
    } catch (cause) {
      setError(clientErrorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  /**
   * 重新定位：先让用户挑文件，再把绑定改过去。
   *
   * 用 `ANY_FORMAT` 而不是"原件的媒体类型"：原件不在磁盘上时后端给不出媒体类型，
   * 而这恰恰是最需要重新定位的时候。文件对话框与签名都由主进程管 ——
   * 这里拿到的只是一个路径，后端只认主进程为它签的名字。
   */
  async function relink() {
    setBusy(true);
    setError("");
    try {
      const picked = await window.docmind.dialogs.chooseSource(ANY_FORMAT);
      if (!picked) return;
      const updated = await window.docmind.documents.relinkOriginal(document.id, picked.path);
      applyUpdated(updated);
    } catch (cause) {
      setError(clientErrorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  function applyUpdated(updated: DocumentDetail) {
    appQueryClient.setQueryData(repositoryKeys.document(document.id), updated);
    void appQueryClient.invalidateQueries({
      queryKey: repositoryKeys.documents(document.repositoryId),
    });
    void appQueryClient.invalidateQueries({ queryKey: repositoryKeys.root });
  }

  useEffect(() => {
    setLocate(null);
  }, [document.id]);

  function locateFigure(target: { page: number; bbox: [number, number, number, number] | null }) {
    setLocate(target);
    setSource("original");
    setMode("read");
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
          {document.originalState === "available" || document.originalState === "changed" ? (
            // 只在原件**够得着**时给这个入口。`missing` 时上面的横幅已经给出了唯一正确的
            // 出口（重新定位原件），再摆一个灰掉的按钮只会让人试着点它。
            <button
              aria-label="用系统程序打开原件"
              className="tree-icon-button"
              disabled={busy}
              onClick={() => void openOriginal()}
              title="用系统程序打开原件（在原格式里修改，改完回来重新转换）"
              type="button"
            >
              <SquareArrowOutUpRight aria-hidden="true" size={16} />
            </button>
          ) : null}
          <button
            aria-label="移除文档"
            className="tree-icon-button"
            onClick={() => setDeleting(true)}
            title="移除文档（远端原文不会被删除）"
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
      {originalNotice ? (
        <div className={`original-notice is-${document.originalState}`} role="status">
          <FileWarning aria-hidden="true" size={16} />
          <p>{originalNotice}</p>
          <div className="original-notice-actions">
            {document.originalState === "changed" ? (
              <button
                className="button button-primary"
                disabled={busy}
                onClick={() => setConfirmingReconvert(true)}
                type="button"
              >
                <RefreshCw aria-hidden="true" size={14} />
                {busy ? "处理中…" : "重新转换"}
              </button>
            ) : null}
            <button
              className="button button-secondary"
              disabled={busy}
              onClick={() => void relink()}
              type="button"
            >
              重新定位原件
            </button>
          </div>
        </div>
      ) : null}
      {mode === "read" ? (
        hasOriginal && source === "original" ? (
          <Suspense
            fallback={
              <div className="pdf-original" role="status">
                <p className="pdf-original-loading">正在加载阅读器…</p>
              </div>
            }
          >
            <PdfOriginalView
              byteSize={document.originalByteSize ?? 0}
              documentId={document.id}
              locate={locate}
            />
          </Suspense>
        ) : (
          <article aria-label="文档内容" className="document-view">
            {content.trim() ? (
              <MarkdownBody content={content} />
            ) : (
              <p className="document-view-empty">这篇文档还没有内容。</p>
            )}
            <DocumentFigures
              documentId={document.id}
              figures={document.figures}
              // 没有可定位的原件时不传回调，图就不可点 —— 而不是点下去什么也不发生。
              onLocate={hasOriginal ? locateFigure : undefined}
            />
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
      {confirmingReconvert ? (
        <ReconvertOriginalDialog
          documentTitle={document.title}
          onClose={() => setConfirmingReconvert(false)}
          onConfirm={() => {
            setConfirmingReconvert(false);
            void reconvert();
          }}
          pending={busy}
        />
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
