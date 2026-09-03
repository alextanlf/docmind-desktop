import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppProviders } from "../../renderer/src/app/AppProviders";
import { ImportDialog } from "../../renderer/src/features/imports/ImportDialog";
import { installDocMindApi } from "./test-docmind-api";

const batch = {
  id: "00000000-0000-0000-0000-000000000101",
  sourceKind: "staged_directory" as const,
  repositoryId: "00000000-0000-0000-0000-000000000021",
  state: "awaiting_confirmation" as const,
  discoveryVersion: 3,
  totalCount: 1,
  selectedCount: 1,
  completedCount: 0,
  failedCount: 0,
  skippedCount: 0,
  progress: 0,
  message: "待确认",
  errorCode: null,
  errorMessage: null,
  retryable: false,
  cancelRequested: false,
  createdAt: "2026-09-01T00:00:00Z",
  startedAt: null,
  completedAt: null,
  updatedAt: "2026-09-01T00:00:00Z",
};

describe("批量导入", () => {
  beforeEach(() => vi.clearAllMocks());

  it("reviews selected candidates and sends the discovery version with decisions", async () => {
    const api = installDocMindApi({
      sources: { stageDirectory: vi.fn().mockResolvedValue({ collectionId: "00000000-0000-0000-0000-000000000099", displayName: "docs", itemCount: 1, totalBytes: 20 }) },
      batches: {
        create: vi.fn().mockResolvedValue(batch),
        listItems: vi.fn().mockResolvedValue({ items: [{ id: "00000000-0000-0000-0000-000000000111", batchId: batch.id, ordinal: 0, title: "a.md", displayPath: "docs/a.md", mediaType: "text/markdown", sizeBytes: 20, sourceRevision: "r1", allowedActions: ["create"], selected: true, decision: "create", state: "discovered", importJobId: null, errorCode: null, errorMessage: null, retryable: false }], nextCursor: null }),
      },
    });
    render(<AppProviders><ImportDialog open onClose={vi.fn()} /></AppProviders>);
    await fireEvent.click(screen.getByRole("radio", { name: "批量" }));
    await fireEvent.click(screen.getByRole("button", { name: "选择目录" }));
    expect(await screen.findByText("docs/a.md")).toBeInTheDocument();
    await fireEvent.click(screen.getByRole("checkbox", { name: "选择 docs/a.md" }));
    await fireEvent.click(screen.getByRole("button", { name: "确认导入" }));
    await waitFor(() => expect(api.batches.confirm).toHaveBeenCalledWith(batch.id, expect.objectContaining({ discoveryVersion: 3 })));
  });
});
