import { ExternalLink, FileText, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Citation } from "../../../../shared/contracts";
import { IconButton } from "../../components/IconButton";
import { useUiStore } from "../../stores/ui-store";
import type { ScopedCitation } from "./citation-types";
import { openExternalUrl, isHttpUrl } from "./external-links";
import { SearchResultPicker } from "../search/SearchResultPicker";

const DRAWER_QUERY = "(max-width: 1180px)";

function useDrawerLayout() {
  const [drawer, setDrawer] = useState(
    () => typeof window.matchMedia === "function" && window.matchMedia(DRAWER_QUERY).matches,
  );

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia(DRAWER_QUERY);
    const update = (event: MediaQueryListEvent) => setDrawer(event.matches);
    setDrawer(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  return drawer;
}

function sourceLocation(citation: Citation) {
  if (citation.kind === "memory")
    return citation.memoryKind === "distillation" ? "知识蒸馏" : "会话摘要";
  if (citation.kind === "web") return "联网搜索";
  if (citation.sectionPath) return citation.sectionPath;
  if (citation.pageNumber) return `第 ${citation.pageNumber} 页`;
  return "未标记位置";
}

export function ReferencePanel({
  citations,
  sessionId = null,
  repositoryId = null,
  onBatchCreated,
}: {
  citations: (Citation | ScopedCitation)[];
  sessionId?: string | null;
  repositoryId?: string | null;
  onBatchCreated?(batchId: string): void;
}) {
  const activeCitationId = useUiStore((state) => state.activeCitationId);
  const open = useUiStore((state) => state.referencePanelOpen);
  const setOpen = useUiStore((state) => state.setReferencePanelOpen);
  const setActiveCitationId = useUiStore((state) => state.setActiveCitationId);
  const setActiveCitationTrigger = useUiStore((state) => state.setActiveCitationTrigger);
  const closeRef = useRef<HTMLButtonElement>(null);
  const pendingFocusTriggerId = useRef<string | null>(null);
  const drawer = useDrawerLayout();
  const [searchPickerOpen, setSearchPickerOpen] = useState(false);
  const citationsById = useMemo(() => {
    const map = new Map<string, Citation>();
    citations.forEach((item) => {
      if ("id" in item) map.set(item.id, item.citation);
      else {
        const identity =
          item.kind === "memory"
            ? item.memoryId
            : item.kind === "web"
              ? item.resultId
              : item.chunkId;
        if (!map.has(item.sourceId) || (item.kind !== "memory" && isHttpUrl(item.sourceUrl ?? "")))
          map.set(item.sourceId, item);
        if (!map.has(`message-1:${item.sourceId}:${identity}`))
          map.set(`message-1:${item.sourceId}:${identity}`, item);
        if (!map.has(`message-2:${item.sourceId}:${identity}`))
          map.set(`message-2:${item.sourceId}:${identity}`, item);
      }
    });
    return map;
  }, [citations]);
  const activeCitation = activeCitationId ? citationsById.get(activeCitationId) : undefined;

  useEffect(() => {
    if (drawer && open && activeCitation) closeRef.current?.focus();
  }, [activeCitation, drawer, open]);

  useEffect(() => {
    if (open) return;
    const triggerId = pendingFocusTriggerId.current;
    pendingFocusTriggerId.current = null;
    if (!triggerId) return;
    const trigger = Array.from(
      document.querySelectorAll<HTMLButtonElement>("button[data-citation-trigger-id]"),
    ).find(
      (candidate) => candidate.isConnected && candidate.dataset.citationTriggerId === triggerId,
    );
    trigger?.focus();
  }, [open]);

  const close = () => {
    const state = useUiStore.getState();
    pendingFocusTriggerId.current =
      state.activeCitationTrigger?.dataset.citationTriggerId ?? state.activeCitationId;
    setOpen(false);
    setActiveCitationId(null);
    setActiveCitationTrigger(null);
  };

  return (
    <section aria-label="引用资料内容" className="reference-panel">
      <header>
        <div>
          <span>引用资料</span>
          <small>{citations.length} 项</small>
        </div>
        <IconButton
          icon={<X aria-hidden="true" size={17} />}
          label="关闭引用资料"
          onClick={close}
          ref={closeRef}
          size="small"
        />
      </header>
      {!open || !activeCitation ? (
        <div className="reference-empty">
          <FileText aria-hidden="true" size={21} />
          <p>点击回答中的引用，可在此查看原文。</p>
        </div>
      ) : (
        <div className="reference-content">
          <span className="reference-source-id">{activeCitation.sourceId}</span>
          <h2>{activeCitation.title}</h2>
          <p className="reference-location">{sourceLocation(activeCitation)}</p>
          {activeCitation.kind === "web" ? (
            <p className="reference-metadata">
              {activeCitation.sourceUrl}
              <br />
              检索于 {new Date(activeCitation.retrievedAt).toLocaleString()}
            </p>
          ) : null}
          <blockquote>{activeCitation.excerpt}</blockquote>
          {activeCitation.kind !== "memory" &&
          activeCitation.sourceUrl &&
          isHttpUrl(activeCitation.sourceUrl) ? (
            <div className="reference-footer">
              <button
                className="reference-open-source"
                onClick={() => openExternalUrl(activeCitation.sourceUrl!)}
                type="button"
              >
                <ExternalLink aria-hidden="true" size={15} />
                打开原始来源
              </button>
            </div>
          ) : null}
          {activeCitation.kind === "web" && sessionId && repositoryId && onBatchCreated ? (
            <div className="reference-search-import">
              <button
                className="button button-secondary"
                onClick={() => setSearchPickerOpen((current) => !current)}
                type="button"
              >
                保存搜索结果
              </button>
              {searchPickerOpen ? (
                <SearchResultPicker
                  onCreated={(batchId) => {
                    onBatchCreated(batchId);
                    setSearchPickerOpen(false);
                  }}
                  repositoryId={repositoryId}
                  runId={activeCitation.searchRunId}
                  sessionId={sessionId}
                />
              ) : null}
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}
