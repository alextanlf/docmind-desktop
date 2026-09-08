import {
  BookOpen,
  ChevronLeft,
  ChevronRight,
  FilePlus2,
  Library,
  PanelRightOpen,
  Settings,
  Square,
  Trash2,
} from "lucide-react";
import { clsx } from "clsx";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { SessionSummary } from "../../../shared/contracts";
import { IconButton } from "../components/IconButton";
import { ChatPanel } from "../features/chat/ChatPanel";
import { RepositoryScope } from "../features/chat/RepositoryScope";
import { SessionList } from "../features/chat/SessionList";
import {
  useDeleteSessionMutation,
  useEndSessionMutation,
  useMessagesQuery,
} from "../features/chat/chat.queries";
import { ImportDialog } from "../features/imports/ImportDialog";
import { ImportProgress } from "../features/imports/ImportProgress";
import { BatchProgress } from "../features/imports/BatchProgress";
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
import { MemoryView } from "../features/memory/MemoryView";
import { useChatStreamStore } from "../stores/chat-stream-store";
import { useUiStore } from "../stores/ui-store";

const FORCED_RAIL_QUERY = "(max-width: 1000px)";

function useForcedIconRail(onBeforeForce: () => void) {
  const [forced, setForced] = useState(
    () => typeof window.matchMedia === "function" && window.matchMedia(FORCED_RAIL_QUERY).matches,
  );

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia(FORCED_RAIL_QUERY);
    const update = (event: MediaQueryListEvent) => {
      if (event.matches) onBeforeForce();
      setForced(event.matches);
    };
    setForced(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, [onBeforeForce]);

  return forced;
}

export function Workspace() {
  const activeView = useUiStore((state) => state.activeView);
  const setActiveView = useUiStore((state) => state.setActiveView);
  const sidebarCollapsed = useUiStore((state) => state.sidebarCollapsed);
  const toggleSidebar = useUiStore((state) => state.toggleSidebar);
  const referencePanelOpen = useUiStore((state) => state.referencePanelOpen);
  const setReferencePanelOpen = useUiStore((state) => state.setReferencePanelOpen);
  const openDistillation = useUiStore((state) => state.openDistillation);
  const sidebarToggleRef = useRef<HTMLButtonElement>(null);
  const sidebarLibraryRef = useRef<HTMLDivElement>(null);
  const importNavigationRef = useRef<HTMLButtonElement>(null);
  const sidebarFocusTransferPending = useRef(false);
  const prepareSidebarFocusTransfer = useCallback(() => {
    sidebarFocusTransferPending.current = document.activeElement === sidebarToggleRef.current;
  }, []);
  const forcedIconRail = useForcedIconRail(prepareSidebarFocusTransfer);
  const [importOpen, setImportOpen] = useState(false);
  const [openBatchConfirmation, setOpenBatchConfirmation] = useState(false);
  const [selectedDocumentId, setSelectedDocumentId] = useState<string | null>(null);
  const [selectedSession, setSelectedSession] = useState<SessionSummary | null>(null);
  const [selectedRepositoryIds, setSelectedRepositoryIds] = useState<string[]>([]);
  const [sessionConfirmation, setSessionConfirmation] = useState<"end" | "delete" | null>(null);
  const sessionActionsRef = useRef<HTMLDivElement>(null);
  const jobId = useImportStore((state) => state.jobId);
  const batchId = useImportStore((state) => state.batchId);
  const setBatchId = useImportStore((state) => state.setBatchId);
  const importJob = useImportJobQuery(jobId);
  const selectedDocument = useDocumentQuery(selectedDocumentId);
  const repositories = useRepositoriesQuery();
  const selectedRepository = repositories.data?.find(
    (repository) => repository.id === selectedDocument.data?.repositoryId,
  );
  const sessionMessages = useMessagesQuery(selectedSession?.id ?? null);
  const stream = useChatStreamStore();
  const endSession = useEndSessionMutation();
  const deleteSession = useDeleteSessionMutation();
  const selectedSessionStreaming =
    stream.sessionId === selectedSession?.id && stream.status === "streaming";

  const closeSessionConfirmation = () => {
    setSessionConfirmation(null);
    requestAnimationFrame(() =>
      sessionActionsRef.current?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus(),
    );
  };

  const confirmSessionAction = async () => {
    if (!selectedSession || !sessionConfirmation) return;
    if (sessionConfirmation === "end") {
      setSelectedSession(await endSession.mutateAsync(selectedSession.id));
    } else {
      await deleteSession.mutateAsync(selectedSession.id);
      setSelectedSession(null);
    }
    closeSessionConfirmation();
  };
  useLayoutEffect(() => {
    if (!forcedIconRail || !sidebarFocusTransferPending.current) return;
    sidebarFocusTransferPending.current = false;
    importNavigationRef.current?.focus();
  }, [forcedIconRail]);

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
      data-reference-layout={forcedIconRail ? "drawer" : "grid"}
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
              ref={sidebarToggleRef}
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
          <button
            aria-label="知识库"
            title="知识库"
            type="button"
            onClick={() => {
              setActiveView("workspace");
              sidebarLibraryRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
              window.setTimeout(() => {
                sidebarLibraryRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
              }, 0);
            }}
          >
            <Library aria-hidden="true" size={17} />
            <span>知识库</span>
          </button>
          <button
            aria-label="导入文档"
            onClick={() => setImportOpen(true)}
            ref={importNavigationRef}
            title="导入文档"
            type="button"
          >
            <FilePlus2 aria-hidden="true" size={17} />
            <span>导入文档</span>
          </button>
          <button aria-label="记忆" onClick={() => setActiveView("memory")} type="button">
            <BookOpen aria-hidden="true" size={17} />
            <span>记忆</span>
          </button>
        </nav>
        <div className="sidebar-library" ref={sidebarLibraryRef}>
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
        <div className="workspace-content">
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
          ) : activeView === "memory" ? (
            <MemoryView />
          ) : selectedDocument.data ? (
            <DocumentEditor
              document={selectedDocument.data}
              key={selectedDocument.data.id}
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
                <div className="session-actions" ref={sessionActionsRef}>
                  <button
                    aria-label="知识蒸馏"
                    className="button button-secondary"
                    disabled={selectedSessionStreaming}
                    onClick={() =>
                      void window.docmind.memory
                        .createDistillation(selectedSession.id)
                        .then((draft) => openDistillation(draft.id))
                    }
                    type="button"
                  >
                    知识蒸馏
                  </button>
                  {selectedSession.endedAt ? null : (
                    <button
                      className="button button-secondary"
                      disabled={selectedSessionStreaming || endSession.isPending}
                      onClick={() => setSessionConfirmation("end")}
                      type="button"
                    >
                      <Square aria-hidden="true" size={15} />
                      结束会话
                    </button>
                  )}
                  <button
                    className="icon-button"
                    aria-label="删除会话"
                    disabled={selectedSessionStreaming || deleteSession.isPending}
                    onClick={() => setSessionConfirmation("delete")}
                    type="button"
                  >
                    <Trash2 aria-hidden="true" size={16} />
                  </button>
                </div>
              </header>
              <ChatPanel
                ended={Boolean(selectedSession.endedAt)}
                repositoryIds={selectedRepositoryIds}
                sessionId={selectedSession.id}
              />
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
        </div>
        {importJob.data || batchId ? (
          <section aria-label="导入任务" className="workspace-import-progress">
            {importJob.data ? (
              <ImportProgress
                job={importJob.data}
                onOpenDocument={(documentId) => setSelectedDocumentId(documentId)}
              />
            ) : null}
            {batchId ? <BatchProgress batchId={batchId} /> : null}
          </section>
        ) : null}
      </section>
      <aside aria-label="引用资料" className="workspace-reference w-[320px]">
        <ReferencePanel
          citations={citations}
          onBatchCreated={(id) => {
            setBatchId(id);
            setOpenBatchConfirmation(true);
            setImportOpen(true);
          }}
          repositoryId={selectedRepositoryIds[0] ?? null}
          sessionId={selectedSession?.id ?? null}
        />
      </aside>
      <ImportDialog
        onClose={() => {
          setImportOpen(false);
          setOpenBatchConfirmation(false);
        }}
        onImported={() => {
          setImportOpen(false);
          setOpenBatchConfirmation(false);
        }}
        openBatchConfirmation={openBatchConfirmation}
        open={importOpen}
      />
      {sessionConfirmation ? (
        <div className="modal-backdrop">
          <section
            aria-labelledby="session-confirmation-title"
            aria-modal="true"
            className="confirmation-dialog"
            role="dialog"
          >
            <h2 id="session-confirmation-title">
              {sessionConfirmation === "end" ? "结束会话" : "删除会话"}
            </h2>
            <p>
              {sessionConfirmation === "end"
                ? "结束后会话将变为只读。"
                : "将删除会话、消息和摘要，此操作不可撤销。"}
            </p>
            <div className="confirmation-actions">
              <button
                className="button button-secondary"
                onClick={closeSessionConfirmation}
                type="button"
              >
                取消
              </button>
              <button
                autoFocus
                className="button button-primary"
                disabled={endSession.isPending || deleteSession.isPending}
                onClick={() => void confirmSessionAction()}
                type="button"
              >
                {sessionConfirmation === "end" ? "确认结束会话" : "确认删除会话"}
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </main>
  );
}
