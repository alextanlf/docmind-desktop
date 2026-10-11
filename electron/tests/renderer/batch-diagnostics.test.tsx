import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppProviders } from "../../renderer/src/app/AppProviders";
import { appQueryClient } from "../../renderer/src/app/query-client";
import { BatchCandidateStep } from "../../renderer/src/features/imports/BatchCandidateStep";
import { installDocMindApi } from "./test-docmind-api";

/**
 * 「为什么只有这么几项」是用户在候选页上真正会问的问题。
 *
 * 这些用例锁的是三件事：分组顺序稳定、每类原因都带"下一步做什么"、以及地址列表
 * 有上限且**说明**自己被截断了 —— 只列 5 条却不说还有多少，用户会以为抓取只发现
 * 了 5 个。
 */
const batch = {
  id: "00000000-0000-0000-0000-000000000201",
  sourceKind: "web" as const,
  repositoryId: "00000000-0000-0000-0000-000000000021",
  state: "awaiting_confirmation" as const,
  discoveryVersion: 1,
  totalCount: 2,
  selectedCount: 2,
  completedCount: 0,
  failedCount: 0,
  skippedCount: 0,
  progress: 0,
  message: "待确认",
  errorCode: null,
  errorMessage: null,
  retryable: false,
  cancelRequested: false,
  createdAt: "2026-10-11T00:00:00Z",
  startedAt: null,
  completedAt: null,
  updatedAt: "2026-10-11T00:00:00Z",
};

function renderStep() {
  return render(
    <AppProviders>
      <BatchCandidateStep batchId={batch.id} onCancel={vi.fn()} onConfirm={vi.fn()} />
    </AppProviders>,
  );
}

describe("批量导入的拒绝诊断", () => {
  beforeEach(() => {
    appQueryClient.clear();
  });

  it("按原因分组，条数多的在前，并给出下一步", async () => {
    installDocMindApi({
      batches: {
        get: vi.fn().mockResolvedValue(batch),
        listItems: vi.fn().mockResolvedValue({ items: [], nextCursor: null }),
        diagnostics: vi.fn().mockResolvedValue({
          total: 3,
          truncated: false,
          items: [
            { canonicalUrl: "https://docs.test/a", reason: "http_rejected" },
            { canonicalUrl: "https://docs.test/b", reason: "unreachable" },
            { canonicalUrl: "https://docs.test/c", reason: "unreachable" },
          ],
        }),
      },
    });
    renderStep();

    const summary = await screen.findByText(/另有 3 个页面没有收录/);
    fireEvent.click(summary);
    // 条数多的排在前（2 条 unreachable 在 1 条 http_rejected 之前）。
    const groups = Array.from(document.querySelectorAll(".batch-diagnostics-list > li"));
    expect(groups[0].textContent).toContain("连不上");
    expect(groups[0].textContent).toContain("2");
    expect(groups[1].textContent).toContain("站点拒绝了这次请求");
    // 每一类都必须说清下一步 —— 只说"失败"等于什么都没说。
    expect(screen.getByText(/换镜像或换一个来源/)).toBeVisible();
    expect(screen.getByText(/改用它的官方 API/)).toBeVisible();
  });

  it("地址列表有上限，而且说明自己被截断了", async () => {
    installDocMindApi({
      batches: {
        get: vi.fn().mockResolvedValue(batch),
        listItems: vi.fn().mockResolvedValue({ items: [], nextCursor: null }),
        diagnostics: vi.fn().mockResolvedValue({
          // total 比实际拿到的条数多：列表被服务端截断，与下面"每组最多 5 条"是两回事。
          total: 20,
          truncated: true,
          items: Array.from({ length: 7 }, (_, index) => ({
            canonicalUrl: `https://docs.test/page-${index}`,
            reason: "unreachable" as const,
          })),
        }),
      },
    });
    renderStep();

    const summary = await screen.findByText(/另有 20 个页面没有收录/);
    fireEvent.click(summary);
    expect(screen.getByText("…另有 2 个")).toBeVisible();
    // 接口截断时也要讲出来，否则用户会以为"就这么点"。
    expect(screen.getByText(/列表只列出前 7 条地址/)).toBeVisible();
    expect(screen.queryByText("https://docs.test/page-6")).toBeNull();
  });

  it("没有页面被拒时什么都不显示", async () => {
    installDocMindApi({
      batches: {
        get: vi.fn().mockResolvedValue(batch),
        listItems: vi.fn().mockResolvedValue({ items: [], nextCursor: null }),
        diagnostics: vi.fn().mockResolvedValue({ total: 0, truncated: false, items: [] }),
      },
    });
    renderStep();

    await screen.findByText(/发现 2 项/);
    expect(screen.queryByText(/没有收录/)).toBeNull();
  });
});
