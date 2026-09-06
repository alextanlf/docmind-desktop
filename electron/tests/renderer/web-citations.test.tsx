import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ReferencePanel } from "../../renderer/src/features/references/ReferencePanel";
import { useUiStore } from "../../renderer/src/stores/ui-store";
describe("web citations", () => {
  it("labels W citations as network evidence", () => {
    const citation = {
      kind: "web" as const,
      sourceId: "W1",
      searchRunId: "00000000-0000-0000-0000-000000000051",
      resultId: "00000000-0000-0000-0000-000000000052",
      title: "Web",
      excerpt: "evidence",
      sourceUrl: "https://example.test",
      retrievedAt: "2026-09-04T00:00:00Z",
    };
    useUiStore.setState({ activeCitationId: "W1", referencePanelOpen: true });
    render(<ReferencePanel citations={[citation]} />);
    expect(screen.getByText("联网搜索")).toBeDefined();
  });
});
