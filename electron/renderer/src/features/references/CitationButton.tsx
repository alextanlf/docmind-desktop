import { FileText } from "lucide-react";
import type { Citation } from "../../../../shared/contracts";
import { useUiStore } from "../../stores/ui-store";

export function CitationButton({ citation }: { citation: Citation }) {
  const setActiveCitationId = useUiStore((state) => state.setActiveCitationId);
  const setReferencePanelOpen = useUiStore((state) => state.setReferencePanelOpen);

  return (
    <button
      aria-label={`查看引用 ${citation.sourceId}`}
      className="citation-button"
      data-citation-id={citation.sourceId}
      onClick={() => {
        setActiveCitationId(citation.sourceId);
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
