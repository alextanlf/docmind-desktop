import { FileText } from "lucide-react";
import { useUiStore } from "../../stores/ui-store";
import type { ScopedCitation } from "./citation-types";

export function CitationButton({ scoped }: { scoped: ScopedCitation }) {
  const { citation } = scoped;
  const setActiveCitationId = useUiStore((state) => state.setActiveCitationId);
  const setActiveCitationTrigger = useUiStore((state) => state.setActiveCitationTrigger);
  const setReferencePanelOpen = useUiStore((state) => state.setReferencePanelOpen);

  return (
    <button
      aria-label={`查看引用 ${citation.sourceId}`}
      className="citation-button"
      data-citation-id={scoped.id}
      onClick={(event) => {
        setActiveCitationId(scoped.id);
        setActiveCitationTrigger(event.currentTarget);
        setReferencePanelOpen(true);
      }}
      title={`查看引用 ${citation.sourceId}`}
      type="button"
    >
      <FileText aria-hidden="true" size={13} />
      {citation.sourceId}
    </button>
  );
}
