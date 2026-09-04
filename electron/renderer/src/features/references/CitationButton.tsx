import { Brain, FileText, Globe } from "lucide-react";
import { useUiStore } from "../../stores/ui-store";
import type { ScopedCitation } from "./citation-types";

export function CitationButton({
  scoped,
  triggerId = scoped.id,
}: {
  scoped: ScopedCitation;
  triggerId?: string;
}) {
  const { citation } = scoped;
  const setActiveCitationId = useUiStore((state) => state.setActiveCitationId);
  const setActiveCitationTrigger = useUiStore((state) => state.setActiveCitationTrigger);
  const setReferencePanelOpen = useUiStore((state) => state.setReferencePanelOpen);
  const SourceIcon = citation.kind === "memory" ? Brain : citation.kind === "web" ? Globe : FileText;

  return (
    <button
      aria-label={`查看引用 ${citation.sourceId}`}
      className="citation-button"
      data-citation-id={scoped.id}
      data-citation-trigger-id={triggerId}
      onClick={(event) => {
        setActiveCitationId(scoped.id);
        setActiveCitationTrigger(event.currentTarget);
        setReferencePanelOpen(true);
      }}
      title={`查看引用 ${citation.sourceId}`}
      type="button"
    >
      <SourceIcon aria-hidden="true" size={13} />
      {citation.sourceId}
    </button>
  );
}
