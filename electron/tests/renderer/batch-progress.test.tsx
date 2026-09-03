import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppProviders } from "../../renderer/src/app/AppProviders";
import { BatchProgress } from "../../renderer/src/features/imports/BatchProgress";
import { installDocMindApi } from "./test-docmind-api";

describe("批量导入进度", () => {
  beforeEach(() => vi.clearAllMocks());

  it("fetches the current batch before replaying progress events", async () => {
    const get = vi.fn().mockResolvedValue({ id: "00000000-0000-0000-0000-000000000101", state: "running", progress: 20, discoveryVersion: 3, totalCount: 1, selectedCount: 1, completedCount: 0, failedCount: 0, skippedCount: 0, message: "运行中", sourceKind: "staged_directory", repositoryId: "00000000-0000-0000-0000-000000000021", errorCode: null, errorMessage: null, retryable: false, cancelRequested: false, createdAt: "2026-09-01T00:00:00Z", startedAt: null, completedAt: null, updatedAt: "2026-09-01T00:00:00Z" });
    const api = installDocMindApi({ batches: { get, subscribe: vi.fn().mockReturnValue({ cancel: vi.fn(), detach: vi.fn() }) } });
    render(<AppProviders><BatchProgress batchId="00000000-0000-0000-0000-000000000101" /></AppProviders>);
    await waitFor(() => expect(get).toHaveBeenCalledWith("00000000-0000-0000-0000-000000000101"));
    await waitFor(() => expect(api.batches.subscribe).toHaveBeenCalledWith("00000000-0000-0000-0000-000000000101", 7, expect.any(Function)));
    expect(screen.getByRole("progressbar", { name: "批量导入进度" })).toBeInTheDocument();
  });
});
