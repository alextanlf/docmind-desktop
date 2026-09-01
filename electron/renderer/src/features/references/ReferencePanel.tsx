import { ExternalLink, FileText, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Citation } from "../../../../shared/contracts";
import { IconButton } from "../../components/IconButton";
import { useUiStore } from "../../stores/ui-store";

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

export function ReferencePanel({ citations }: { citations: Citation[] }) {
  const activeCitationId = useUiStore((state) => state.activeCitationId);
  const open = useUiStore((state) => state.referencePanelOpen);
  const setOpen = useUiStore((state) => state.setReferencePanelOpen);
  const setActiveCitationId = useUiStore((state) => state.setActiveCitationId);
  const closeRef = useRef<HTMLButtonElement>(null);
  const drawer = useDrawerLayout();
  const citationsById = useMemo(
    () => new Map(citations.map((citation) => [citation.sourceId, citation])),
    [citations],
  );
  const activeCitation = activeCitationId ? citationsById.get(activeCitationId) : undefined;

  useEffect(() => {
    if (drawer && open && activeCitation) closeRef.current?.focus();
  }, [activeCitation, drawer, open]);

  const close = () => {
    const returnId = activeCitationId;
    setOpen(false);
    setActiveCitationId(null);
    if (drawer && returnId) {
      Array.from(document.querySelectorAll<HTMLButtonElement>("[data-citation-id]"))
        .find((button) => button.dataset.citationId === returnId)
        ?.focus();
    }
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
          {activeCitation.sourceUrl ? (
            <button
              className="reference-open-source"
              onClick={() => void window.docmind.shell.openExternal(activeCitation.sourceUrl!)}
              type="button"
            >
              <ExternalLink aria-hidden="true" size={15} />
              打开原始来源
            </button>
          ) : null}
        </div>
      )}
    </section>
  );
}
