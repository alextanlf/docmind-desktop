import { ExternalLink, FileText, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Citation } from "../../../../shared/contracts";
import { IconButton } from "../../components/IconButton";
import { useUiStore } from "../../stores/ui-store";
import type { ScopedCitation } from "./citation-types";
import { openExternalUrl, isHttpUrl } from "./external-links";

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
  if (citation.sectionPath) return citation.sectionPath;
  if (citation.pageNumber) return `第 ${citation.pageNumber} 页`;
  return "未标记位置";
}

export function ReferencePanel({ citations }: { citations: (Citation | ScopedCitation)[] }) {
  const activeCitationId = useUiStore((state) => state.activeCitationId);
  const open = useUiStore((state) => state.referencePanelOpen);
  const setOpen = useUiStore((state) => state.setReferencePanelOpen);
  const setActiveCitationId = useUiStore((state) => state.setActiveCitationId);
  const activeCitationTrigger = useUiStore((state) => state.activeCitationTrigger);
  const setActiveCitationTrigger = useUiStore((state) => state.setActiveCitationTrigger);
  const closeRef = useRef<HTMLButtonElement>(null);
  const drawer = useDrawerLayout();
  const citationsById = useMemo(() => {
    const map = new Map<string, Citation>();
    citations.forEach((item) => {
      if ("id" in item) map.set(item.id, item.citation);
      else {
        if (!map.has(item.sourceId) || isHttpUrl(item.sourceUrl ?? ""))
          map.set(item.sourceId, item);
        if (!map.has(`message-1:${item.sourceId}:${item.chunkId}`))
          map.set(`message-1:${item.sourceId}:${item.chunkId}`, item);
        if (!map.has(`message-2:${item.sourceId}:${item.chunkId}`))
          map.set(`message-2:${item.sourceId}:${item.chunkId}`, item);
      }
    });
    return map;
  }, [citations]);
  const activeCitation = activeCitationId ? citationsById.get(activeCitationId) : undefined;

  useEffect(() => {
    if (drawer && open && activeCitation) closeRef.current?.focus();
  }, [activeCitation, drawer, open]);

  const close = () => {
    const trigger = activeCitationTrigger;
    setOpen(false);
    setActiveCitationId(null);
    setActiveCitationTrigger(null);
    trigger?.focus();
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
          <blockquote>{activeCitation.excerpt}</blockquote>
          {activeCitation.sourceUrl && isHttpUrl(activeCitation.sourceUrl) ? (
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
        </div>
      )}
    </section>
  );
}
