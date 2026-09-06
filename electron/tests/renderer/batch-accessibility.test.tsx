import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppProviders } from "../../renderer/src/app/AppProviders";
import { BatchProgress } from "../../renderer/src/features/imports/BatchProgress";
import { installDocMindApi } from "./test-docmind-api";

describe("批量导入可访问性", () => {
  beforeEach(() => vi.clearAllMocks());

  it("exposes a labelled progress region and keyboard-addressable recovery actions", async () => {
    installDocMindApi({
      batches: {
        get: vi.fn().mockResolvedValue({
          id: "00000000-0000-0000-0000-000000000101",
          sourceKind: "staged_directory",
          repositoryId: "00000000-0000-0000-0000-000000000021",
          state: "paused",
          discoveryVersion: 1,
          totalCount: 3,
          selectedCount: 3,
          completedCount: 1,
          failedCount: 0,
          skippedCount: 0,
          progress: 33,
          message: "应用重启导致批次暂停",
          errorCode: null,
          errorMessage: null,
          retryable: false,
          cancelRequested: false,
          createdAt: "2026-09-01T00:00:00Z",
          startedAt: "2026-09-01T00:00:00Z",
          completedAt: null,
          updatedAt: "2026-09-01T00:00:00Z",
          lastEventSequence: 3,
        }),
      },
    });
    render(
      <AppProviders>
        <BatchProgress batchId="00000000-0000-0000-0000-000000000101" />
      </AppProviders>,
    );

    expect(await screen.findByRole("region", { name: "批量导入进度" })).toBeVisible();
    expect(screen.getByRole("progressbar", { name: "批量导入进度" })).toHaveAttribute(
      "aria-valuenow",
      "33",
    );
    await waitFor(() => expect(screen.getByRole("button", { name: "继续批量导入" })).toBeEnabled());
    expect(screen.getByRole("button", { name: "取消批量导入" })).toBeEnabled();
  });
});
