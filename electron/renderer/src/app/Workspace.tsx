import {
  BookOpen,
  ChevronLeft,
  ChevronRight,
  FilePlus2,
  Library,
  MessageSquarePlus,
  PanelRightClose,
  PanelRightOpen,
  Settings,
} from "lucide-react";
import { clsx } from "clsx";
import { useEffect, useState } from "react";
import { IconButton } from "../components/IconButton";
import { ImportDialog } from "../features/imports/ImportDialog";
import { ImportProgress } from "../features/imports/ImportProgress";
import { useImportJobQuery } from "../features/imports/imports.queries";
import { useImportStore } from "../features/imports/import-store";
import { DocumentEditor } from "../features/repositories/DocumentEditor";
import { RepositoryTree } from "../features/repositories/RepositoryTree";
import { useDocumentQuery } from "../features/repositories/repository.queries";
import { SettingsView } from "../features/settings/SettingsView";
import { useUiStore } from "../stores/ui-store";

const FORCED_RAIL_QUERY = "(max-width: 1000px)";

function useForcedIconRail() {
  const [forced, setForced] = useState(
    () => typeof window.matchMedia === "function" && window.matchMedia(FORCED_RAIL_QUERY).matches,
  );

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia(FORCED_RAIL_QUERY);
    const update = (event: MediaQueryListEvent) => setForced(event.matches);
    setForced(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  return forced;
}

export function Workspace() {
  const activeView = useUiStore((state) => state.activeView);
  const setActiveView = useUiStore((state) => state.setActiveView);
  const sidebarCollapsed = useUiStore((state) => state.sidebarCollapsed);
  const toggleSidebar = useUiStore((state) => state.toggleSidebar);
  const referencePanelOpen = useUiStore((state) => state.referencePanelOpen);
  const setReferencePanelOpen = useUiStore((state) => state.setReferencePanelOpen);
  const forcedIconRail = useForcedIconRail();
  const [importOpen, setImportOpen] = useState(false);
  const [selectedDocumentId, setSelectedDocumentId] = useState<string | null>(null);
  const jobId = useImportStore((state) => state.jobId);
  const importJob = useImportJobQuery(jobId);
  const selectedDocument = useDocumentQuery(selectedDocumentId);

  return (
    <main
      aria-label="工作台"
      className={clsx(
        "workspace-grid",
        sidebarCollapsed && "sidebar-is-collapsed",
        !referencePanelOpen && "reference-is-closed",
      )}
    >
      <aside
        aria-label="主导航"
        className={clsx("workspace-sidebar w-[248px]", sidebarCollapsed && "is-collapsed")}
      >
        <div className="brand-row">
          <span className="brand-mark" aria-hidden="true">
            D
          </span>
          <strong>DocMind</strong>
          {forcedIconRail ? null : (
            <IconButton
              icon={
                sidebarCollapsed ? (
                  <ChevronRight aria-hidden="true" size={18} />
                ) : (
                  <ChevronLeft aria-hidden="true" size={18} />
                )
              }
              label={sidebarCollapsed ? "展开侧边栏" : "收起侧边栏"}
              onClick={toggleSidebar}
              size="small"
            />
          )}
        </div>
        <button aria-label="新建会话" className="new-chat-button" title="新建会话" type="button">
          <MessageSquarePlus aria-hidden="true" size={17} />
          <span>新建会话</span>
        </button>
        <nav className="nav-list" aria-label="功能导航">
          <button aria-label="知识库" title="知识库" type="button">
            <Library aria-hidden="true" size={17} />
            <span>知识库</span>
          </button>
          <button
            aria-label="导入文档"
            onClick={() => setImportOpen(true)}
            title="导入文档"
            type="button"
          >
            <FilePlus2 aria-hidden="true" size={17} />
            <span>导入文档</span>
          </button>
        </nav>
        <div className="sidebar-library">
          <RepositoryTree
            onOpenDocument={(documentId) => {
              setSelectedDocumentId(documentId);
            }}
          />
        </div>
        <button
          aria-label="设置"
          className={clsx("settings-nav", activeView === "settings" && "is-active")}
          onClick={() => setActiveView(activeView === "settings" ? "workspace" : "settings")}
          title="设置"
          type="button"
        >
          <Settings aria-hidden="true" size={17} />
          <span>设置</span>
        </button>
      </aside>
      <section
        className="workspace-main"
        aria-label={activeView === "settings" ? "设置内容" : "对话工作区"}
      >
        {!referencePanelOpen ? (
          <IconButton
            className="reference-reopen"
            icon={<PanelRightOpen aria-hidden="true" size={17} />}
            label="打开引用资料"
            onClick={() => setReferencePanelOpen(true)}
            size="small"
          />
        ) : null}
        {activeView === "settings" ? (
          <SettingsView />
        ) : selectedDocument.data ? (
          <DocumentEditor
            document={selectedDocument.data}
            onClose={() => setSelectedDocumentId(null)}
          />
        ) : (
          <div className="empty-workspace">
            <BookOpen aria-hidden="true" size={24} />
            <h1>选择知识库开始对话</h1>
            <p>导入文档后，可在这里检索内容并查看引用来源。</p>
            <button
              className="button button-primary"
              onClick={() => setImportOpen(true)}
              type="button"
            >
              <FilePlus2 aria-hidden="true" size={16} />
              导入第一篇文档
            </button>
          </div>
        )}
      </section>
      <aside aria-label="引用资料" className="workspace-reference w-[320px]">
        <header>
          <div>
            <span>引用资料</span>
            <small>0 项</small>
          </div>
          <IconButton
            icon={<PanelRightClose aria-hidden="true" size={17} />}
            label="关闭引用资料"
            onClick={() => setReferencePanelOpen(false)}
            size="small"
          />
        </header>
        <div className="reference-empty">
          <BookOpen aria-hidden="true" size={21} />
          <p>点击回答中的引用，可在此查看原文。</p>
        </div>
      </aside>
      <ImportDialog
        onClose={() => setImportOpen(false)}
        onImported={() => setImportOpen(false)}
        open={importOpen}
      />
      {importJob.data ? (
        <div className="workspace-import-progress">
          <ImportProgress
            job={importJob.data}
            onOpenDocument={(documentId) => setSelectedDocumentId(documentId)}
          />
        </div>
      ) : null}
    </main>
  );
}
