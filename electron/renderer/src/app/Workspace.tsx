import {
  BookOpen,
  ChevronLeft,
  ChevronRight,
  FilePlus2,
  Library,
  PanelRightOpen,
  Settings,
} from "lucide-react";
import { clsx } from "clsx";
import { useEffect, useMemo, useState } from "react";
import type { SessionSummary } from "../../../shared/contracts";
import { IconButton } from "../components/IconButton";
import { ChatPanel } from "../features/chat/ChatPanel";
import { RepositoryScope } from "../features/chat/RepositoryScope";
import { SessionList } from "../features/chat/SessionList";
import { useMessagesQuery } from "../features/chat/chat.queries";
import { ImportDialog } from "../features/imports/ImportDialog";
import { ImportProgress } from "../features/imports/ImportProgress";
import { useImportJobQuery } from "../features/imports/imports.queries";
import { useImportStore } from "../features/imports/import-store";
import { DocumentEditor } from "../features/repositories/DocumentEditor";
import { RepositoryTree } from "../features/repositories/RepositoryTree";
import { ReferencePanel } from "../features/references/ReferencePanel";
import { citationIdentity, type ScopedCitation } from "../features/references/citation-types";
import {
  useDocumentQuery,
  useRepositoriesQuery,
} from "../features/repositories/repository.queries";
import { SettingsView } from "../features/settings/SettingsView";
import { useChatStreamStore } from "../stores/chat-stream-store";
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
  const [selectedSession, setSelectedSession] = useState<SessionSummary | null>(null);
  const [selectedRepositoryIds, setSelectedRepositoryIds] = useState<string[]>([]);
  const jobId = useImportStore((state) => state.jobId);
  const importJob = useImportJobQuery(jobId);
  const selectedDocument = useDocumentQuery(selectedDocumentId);
  const repositories = useRepositoriesQuery();
  const selectedRepository = repositories.data?.find(
    (repository) => repository.id === selectedDocument.data?.repositoryId,
  );
  const sessionMessages = useMessagesQuery(selectedSession?.id ?? null);
  const stream = useChatStreamStore();
  const citations = useMemo(() => {
    const all: ScopedCitation[] = (sessionMessages.data ?? []).flatMap((message) =>
      message.citations.map((citation) => ({
        id: citationIdentity(message.id, citation),
        citation,
      })),
    );
    if (stream.sessionId === selectedSession?.id && stream.requestId)
      all.push(
        ...stream.citations.map((citation) => ({
          id: citationIdentity(stream.requestId!, citation),
          citation,
        })),
      );
    const known = new Set<string>();
    return all.filter((citation) => {
      const key = citation.id;
      if (known.has(key)) return false;
      known.add(key);
      return true;
    });
  }, [
    selectedSession?.id,
    sessionMessages.data,
    stream.citations,
    stream.requestId,
    stream.sessionId,
  ]);

  const selectSession = (session: SessionSummary) => {
    if (selectedSession?.id !== session.id) {
      useChatStreamStore.getState().cancelForSession(selectedSession?.id ?? null);
      setSelectedSession(session);
      setSelectedRepositoryIds(session.repositoryIds);
      setSelectedDocumentId(null);
    }
  };

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
        <SessionList
          onSessionSelect={selectSession}
          repositories={repositories.data ?? []}
          collapsed={sidebarCollapsed || forcedIconRail}
          selectedRepositoryIds={selectedRepositoryIds}
          selectedSessionId={selectedSession?.id}
        />
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
            repositoryName={selectedRepository?.name}
          />
        ) : selectedSession ? (
          <div className="chat-workspace">
            <header className="chat-toolbar">
              <RepositoryScope
                onChange={setSelectedRepositoryIds}
                repositories={repositories.data ?? []}
                selectedRepositoryIds={selectedRepositoryIds}
              />
            </header>
            <ChatPanel repositoryIds={selectedRepositoryIds} sessionId={selectedSession.id} />
          </div>
        ) : (
          <div className="chat-workspace">
            <header className="chat-toolbar">
              <RepositoryScope
                onChange={setSelectedRepositoryIds}
                repositories={repositories.data ?? []}
                selectedRepositoryIds={selectedRepositoryIds}
              />
            </header>
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
          </div>
        )}
      </section>
      <aside aria-label="引用资料" className="workspace-reference w-[320px]">
        <ReferencePanel citations={citations} />
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
