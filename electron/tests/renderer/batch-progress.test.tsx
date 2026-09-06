import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { BatchImport, BatchItem } from "../../shared/contracts";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppProviders } from "../../renderer/src/app/AppProviders";
import { BatchProgress } from "../../renderer/src/features/imports/BatchProgress";
import { installDocMindApi } from "./test-docmind-api";

describe("批量导入进度", () => {
  beforeEach(() => vi.clearAllMocks());

  it("fetches the current batch before replaying progress events", async () => {
    const get = vi.fn().mockResolvedValue({
      id: "00000000-0000-0000-0000-000000000101",
      state: "running",
      progress: 20,
      discoveryVersion: 3,
      totalCount: 1,
      selectedCount: 1,
      completedCount: 0,
      failedCount: 0,
      skippedCount: 0,
      message: "运行中",
      sourceKind: "staged_directory",
      repositoryId: "00000000-0000-0000-0000-000000000021",
      errorCode: null,
      errorMessage: null,
      retryable: false,
      cancelRequested: false,
      createdAt: "2026-09-01T00:00:00Z",
      startedAt: null,
      completedAt: null,
      updatedAt: "2026-09-01T00:00:00Z",
      lastEventSequence: 42,
    });
    let onEvent: ((event: any) => void) | undefined;
    const api = installDocMindApi({
      batches: {
        get,
        subscribe: vi.fn((_id, _after, callback) => {
          onEvent = callback;
          return { requestId: "batch-test", cancel: vi.fn(), detach: vi.fn() };
        }),
      },
    });
    render(
      <AppProviders>
        <BatchProgress batchId="00000000-0000-0000-0000-000000000101" />
      </AppProviders>,
    );
    await waitFor(() => expect(get).toHaveBeenCalledWith("00000000-0000-0000-0000-000000000101"));
    await waitFor(() =>
      expect(api.batches.subscribe).toHaveBeenCalledWith(
        "00000000-0000-0000-0000-000000000101",
        42,
        expect.any(Function),
      ),
    );
    expect(screen.getByRole("progressbar", { name: "批量导入进度" })).toBeInTheDocument();
    onEvent?.({
      requestId: "00000000-0000-0000-0000-000000000101",
      type: "progress",
      sequence: 43,
      payload: {
        progress: 100,
        state: "completed",
        stage: "running",
        message: "完成",
        counts: { total: 1, selected: 1, completed: 1, failed: 0, skipped: 0 },
        itemId: null,
        itemState: null,
      },
    });
    await waitFor(() => expect(screen.getByText("1/1 已完成，0 失败，0 跳过")).toBeInTheDocument());
  });
});

const failedBatch: BatchImport = {
  id: "00000000-0000-0000-0000-000000000102",
  repositoryId: "00000000-0000-0000-0000-000000000021",
  sourceKind: "staged_directory",
  state: "completed_with_errors",
  discoveryVersion: 1,
  totalCount: 2,
  selectedCount: 2,
  completedCount: 1,
  failedCount: 1,
  skippedCount: 0,
  progress: 100,
  message: "部分失败",
  errorCode: null,
  errorMessage: null,
  retryable: false,
  cancelRequested: false,
  createdAt: "2026-09-01T00:00:00Z",
  updatedAt: "2026-09-01T00:00:00Z",
  startedAt: null,
  completedAt: null,
  lastEventSequence: 45,
};
const failedItem: BatchItem = {
  id: "00000000-0000-0000-0000-000000000112",
  batchId: failedBatch.id,
  ordinal: 1,
  title: "b.md",
  displayPath: "docs/b.md",
  mediaType: "text/markdown",
  sizeBytes: 20,
  sourceRevision: "r1",
  allowedActions: ["create", "skip"],
  selected: true,
  decision: "create",
  state: "failed",
  importJobId: "00000000-0000-0000-0000-000000000113",
  errorCode: "IMPORT_FAILED",
  errorMessage: "失败",
  retryable: true,
};

function renderProgress() {
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <BatchProgress batchId={failedBatch.id} />
    </QueryClientProvider>,
  );
}

it("recovers the failed items page before submitting every retryable child", async () => {
  const second = { ...failedItem, id: "00000000-0000-0000-0000-000000000115" };
  const listItems = vi
    .fn()
    .mockResolvedValueOnce({ items: [failedItem], nextCursor: "page2" })
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValue({ items: [second], nextCursor: null });
  const api = installDocMindApi({
    batches: { get: vi.fn().mockResolvedValue(failedBatch), listItems },
  });
  renderProgress();
  expect(await screen.findByRole("alert")).toHaveTextContent("批次项加载失败");
  expect(screen.getByRole("button", { name: "重试批量导入" })).toBeDisabled();
  expect(api.batches.retry).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "重试加载批次项" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "重试批量导入" })).toBeEnabled());
  expect(listItems.mock.calls.map((call) => call[1])).toEqual([null, "page2", "page2"]);
  fireEvent.click(screen.getByRole("button", { name: "重试批量导入" }));
  await waitFor(() =>
    expect(api.batches.retry).toHaveBeenCalledWith(failedBatch.id, {
      itemIds: [failedItem.id, second.id],
    }),
  );
});

it("offers item loading recovery even when the first page fails", async () => {
  const listItems = vi
    .fn()
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValue({ items: [failedItem], nextCursor: null });
  installDocMindApi({ batches: { get: vi.fn().mockResolvedValue(failedBatch), listItems } });
  renderProgress();
  expect(await screen.findByRole("alert")).toHaveTextContent("批次项加载失败");
  expect(screen.queryByRole("button", { name: "重试批量导入" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "重试加载批次项" }));
  expect(await screen.findByRole("button", { name: "重试批量导入" })).toBeEnabled();
  expect(listItems.mock.calls.map((call) => call[1])).toEqual([null, null]);
});

it("retries persisted failed children after remount and resumes replay from the new snapshot", async () => {
  const running = {
    ...failedBatch,
    state: "running" as const,
    progress: 50,
    lastEventSequence: 47,
  };
  let finishRetry!: (batch: BatchImport) => void;
  const retry = vi.fn(
    () =>
      new Promise<BatchImport>((resolve) => {
        finishRetry = resolve;
      }),
  );
  const cancel = vi.fn();
  const api = installDocMindApi({
    batches: {
      get: vi.fn().mockResolvedValueOnce(failedBatch).mockResolvedValue(running),
      listItems: vi
        .fn()
        .mockResolvedValueOnce({ items: [], nextCursor: "next" })
        .mockResolvedValue({ items: [failedItem], nextCursor: null }),
      retry,
      subscribe: vi.fn(() => ({ requestId: "retry-test", cancel, detach: vi.fn() })),
    },
  });
  renderProgress();
  const button = await screen.findByRole("button", { name: "重试批量导入" });
  expect(api.batches.listItems).toHaveBeenCalledWith(failedBatch.id, "next");
  fireEvent.click(button);
  await waitFor(() => expect(button).toBeDisabled());
  fireEvent.click(button);
  expect(retry).toHaveBeenCalledTimes(1);
  expect(retry).toHaveBeenCalledWith(failedBatch.id, { itemIds: [failedItem.id] });
  await act(async () => finishRetry(running));
  await waitFor(() =>
    expect(api.batches.subscribe).toHaveBeenLastCalledWith(
      failedBatch.id,
      47,
      expect.any(Function),
    ),
  );
  expect(cancel).toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "重试批量导入" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "取消批量导入" })).toBeInTheDocument();
});

it.each(["failed", "cancelled", "completed"] as const)(
  "does not offer child retry for terminal %s batches",
  async (state) => {
    const api = installDocMindApi({
      batches: { get: vi.fn().mockResolvedValue({ ...failedBatch, state, retryable: true }) },
    });
    renderProgress();
    await screen.findByRole("progressbar");
    expect(screen.queryByRole("button", { name: "重试批量导入" })).not.toBeInTheDocument();
    expect(api.batches.retry).not.toHaveBeenCalled();
  },
);

it("does not retry nonretryable or unreserved children", async () => {
  const listItems = vi.fn().mockResolvedValue({
    items: [
      { ...failedItem, retryable: false },
      { ...failedItem, id: "00000000-0000-0000-0000-000000000114", importJobId: null },
    ],
    nextCursor: null,
  });
  installDocMindApi({ batches: { get: vi.fn().mockResolvedValue(failedBatch), listItems } });
  renderProgress();
  await waitFor(() => expect(listItems).toHaveBeenCalled());
  expect(screen.queryByRole("button", { name: "重试批量导入" })).not.toBeInTheDocument();
});

it("shows a failed retry and permits another attempt without an unhandled rejection", async () => {
  const api = installDocMindApi({
    batches: {
      get: vi.fn().mockResolvedValue(failedBatch),
      listItems: vi.fn().mockResolvedValue({ items: [failedItem], nextCursor: null }),
      retry: vi
        .fn()
        .mockRejectedValueOnce(new Error("offline"))
        .mockResolvedValue({ ...failedBatch, state: "running" }),
    },
  });
  renderProgress();
  fireEvent.click(await screen.findByRole("button", { name: "重试批量导入" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("操作失败，请重试");
  fireEvent.click(screen.getByRole("button", { name: "重试批量导入" }));
  await waitFor(() => expect(api.batches.retry).toHaveBeenCalledTimes(2));
});
