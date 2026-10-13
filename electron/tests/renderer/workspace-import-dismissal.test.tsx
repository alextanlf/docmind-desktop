import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProviders } from "../../renderer/src/app/AppProviders";
import { Workspace } from "../../renderer/src/app/Workspace";
import { appQueryClient } from "../../renderer/src/app/query-client";
import {
  batchSettledDismissDelayMs,
  importSuccessDismissDelayMs,
} from "../../renderer/src/features/imports/import-dismissal";
import { useImportStore } from "../../renderer/src/features/imports/import-store";
import { useChatStreamStore } from "../../renderer/src/stores/chat-stream-store";
import { useUiStore } from "../../renderer/src/stores/ui-store";
import { installDocMindApi, job } from "./test-docmind-api";

/**
 * 「导入完成之后进度栏不要常驻」的行为边界：成功自己让位，失败/取消留着等人处理。
 *
 * 延迟从外面注入（`dismissDelays`），所以这里用真实定时器等它自己收 —— 换成假时钟的话，
 * React Query 落地数据也走微任务与定时器，很难分清「进度栏没出现」和「已经收掉了」。
 */
const settleMs = 40;
const batchId = "00000000-0000-0000-0000-000000000101";

function batch(state: string) {
  return {
    id: batchId,
    state,
    progress: state === "running" ? 50 : 100,
    message: state === "running" ? "批量导入中" : "批量导入完成",
    completedCount: 2,
    selectedCount: 2,
    failedCount: 0,
    skippedCount: 0,
    lastEventSequence: 1,
  };
}

function renderWorkspace() {
  return render(
    <AppProviders>
      <Workspace
        dismissDelays={{ importAfterSuccessMs: settleMs, batchAfterSettledMs: settleMs }}
      />
    </AppProviders>,
  );
}

/** 进度栏出现（说明取数落地了）才算起点，否则「它没出现」会被当成「它收起了」。 */
async function trayAppeared() {
  return screen.findByRole("region", { name: "导入任务" });
}

beforeEach(() => {
  appQueryClient.clear();
  useImportStore.getState().reset();
  useChatStreamStore.getState().reset();
  useUiStore.setState({ activeView: "workspace", referencePanelOpen: false });
});

afterEach(() => {
  appQueryClient.clear();
  useImportStore.getState().reset();
});

describe("导入进度栏的收起", () => {
  it("成功之后自己收起", async () => {
    installDocMindApi({
      imports: {
        get: vi
          .fn()
          .mockResolvedValue({ ...job, state: "completed", progress: 100, message: "导入完成" }),
      },
    });
    useImportStore.setState({ jobId: job.id, batchId: null });
    renderWorkspace();
    await trayAppeared();

    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "导入任务" })).not.toBeInTheDocument(),
    );
    expect(useImportStore.getState().jobId).toBeNull();
  });

  it("还在导入时不收起", async () => {
    installDocMindApi({
      imports: {
        get: vi
          .fn()
          .mockResolvedValue({ ...job, state: "indexing", progress: 40, message: "正在索引" }),
      },
    });
    useImportStore.setState({ jobId: job.id, batchId: null });
    renderWorkspace();
    await trayAppeared();

    // 等过注入的收起时长：跑着的任务不该被这条规则碰到。
    await new Promise((resolve) => setTimeout(resolve, settleMs * 3));
    expect(useImportStore.getState().jobId).toBe(job.id);
    expect(screen.getByRole("region", { name: "导入任务" })).toBeInTheDocument();
  });

  it("失败之后留在原地，直到用户自己关掉", async () => {
    installDocMindApi({
      imports: {
        get: vi.fn().mockResolvedValue({
          ...job,
          state: "failed",
          retryable: true,
          message: "导入失败",
          errorMessage: "解析失败",
        }),
      },
    });
    useImportStore.setState({ jobId: job.id, batchId: null });
    renderWorkspace();
    await trayAppeared();

    await new Promise((resolve) => setTimeout(resolve, settleMs * 3));
    // 错误原因与「重试导入」是下一步操作的入口，收掉等于把入口藏起来。
    expect(screen.getByRole("button", { name: "重试导入" })).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "收起导入进度" }));
    expect(useImportStore.getState().jobId).toBeNull();
    expect(screen.queryByRole("region", { name: "导入任务" })).not.toBeInTheDocument();
  });

  it("打开刚导入的文档时顺手收起进度栏", async () => {
    installDocMindApi({
      imports: {
        get: vi.fn().mockResolvedValue({
          ...job,
          state: "completed",
          progress: 100,
          message: "导入完成",
          documentId: "00000000-0000-0000-0000-000000000042",
        }),
      },
    });
    useImportStore.setState({ jobId: job.id, batchId: null });
    renderWorkspace();
    await trayAppeared();

    fireEvent.click(screen.getByRole("button", { name: "查看文档" }));
    expect(useImportStore.getState().jobId).toBeNull();
    expect(screen.queryByRole("region", { name: "导入任务" })).not.toBeInTheDocument();
  });

  it("批量导入跑完就收起，部分失败也收起", async () => {
    for (const state of ["completed", "completed_with_errors"]) {
      installDocMindApi({ batches: { get: vi.fn().mockResolvedValue(batch(state)) } });
      useImportStore.setState({ jobId: null, batchId });
      const view = renderWorkspace();
      await trayAppeared();

      await waitFor(() => expect(useImportStore.getState().batchId).toBeNull());
      view.unmount();
      appQueryClient.clear();
      useImportStore.getState().reset();
    }
  });

  it("批量导入失败时留着", async () => {
    installDocMindApi({ batches: { get: vi.fn().mockResolvedValue(batch("failed")) } });
    useImportStore.setState({ jobId: null, batchId });
    renderWorkspace();
    await trayAppeared();

    await new Promise((resolve) => setTimeout(resolve, settleMs * 3));
    expect(useImportStore.getState().batchId).toBe(batchId);
    expect(screen.getByRole("region", { name: "导入任务" })).toBeInTheDocument();
  });

  it("真实延迟是让人来得及看一眼的秒级，而不是零", () => {
    // 这两个常量是「成功也要停一下」的量化：取 0 会让「查看文档」来不及点。
    expect(importSuccessDismissDelayMs).toBeGreaterThanOrEqual(2000);
    expect(batchSettledDismissDelayMs).toBeGreaterThanOrEqual(importSuccessDismissDelayMs);
  });
});
