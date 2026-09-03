import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppProviders } from "../../renderer/src/app/AppProviders";
import { BatchProgress } from "../../renderer/src/features/imports/BatchProgress";
import { installDocMindApi } from "./test-docmind-api";

describe("批量导入进度", () => {
  beforeEach(() => vi.clearAllMocks());

  it("fetches the current batch before replaying progress events", async () => {
    const get = vi.fn().mockResolvedValue({ id: "00000000-0000-0000-0000-000000000101", state: "running", progress: 20, discoveryVersion: 3, totalCount: 1, selectedCount: 1, completedCount: 0, failedCount: 0, skippedCount: 0, message: "运行中", sourceKind: "staged_directory", repositoryId: "00000000-0000-0000-0000-000000000021", errorCode: null, errorMessage: null, retryable: false, cancelRequested: false, createdAt: "2026-09-01T00:00:00Z", startedAt: null, completedAt: null, updatedAt: "2026-09-01T00:00:00Z", lastEventSequence: 42 });
    let onEvent: ((event: any) => void) | undefined;
    const api = installDocMindApi({ batches: { get, subscribe: vi.fn((_id, _after, callback) => { onEvent = callback; return { cancel: vi.fn(), detach: vi.fn() }; }) } });
    render(<AppProviders><BatchProgress batchId="00000000-0000-0000-0000-000000000101" /></AppProviders>);
    await waitFor(() => expect(get).toHaveBeenCalledWith("00000000-0000-0000-0000-000000000101"));
    await waitFor(() => expect(api.batches.subscribe).toHaveBeenCalledWith("00000000-0000-0000-0000-000000000101", 42, expect.any(Function)));
    expect(screen.getByRole("progressbar", { name: "批量导入进度" })).toBeInTheDocument();
    onEvent?.({ requestId: "00000000-0000-0000-0000-000000000101", type: "progress", sequence: 43, payload: { progress: 100, state: "completed", message: "完成", counts: { total: 1, selected: 1, completed: 1, failed: 0, skipped: 0 } } });
    await waitFor(() => expect(screen.getByText("1/1 已完成，0 失败，0 跳过")).toBeInTheDocument());
  });
});
