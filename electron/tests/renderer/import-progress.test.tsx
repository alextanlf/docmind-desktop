import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppProviders } from "../../renderer/src/app/AppProviders";
import { appQueryClient } from "../../renderer/src/app/query-client";
import { ImportProgress } from "../../renderer/src/features/imports/ImportProgress";
import { installDocMindApi, job } from "./test-docmind-api";

describe("导入进度", () => {
  beforeEach(() => appQueryClient.clear());

  it("offers retry only when a failed import can be retried", () => {
    installDocMindApi();
    render(
      <AppProviders>
        <ImportProgress job={{ ...job, state: "failed", retryable: true }} />
      </AppProviders>,
    );
    expect(screen.getByRole("button", { name: "重试导入" })).toBeVisible();
  });

  it("replays only newer progress events and releases its stream listener on unmount", async () => {
    let eventHandler:
      ((event: { sequence: number; payload: Record<string, unknown> }) => void) | undefined;
    const cancel = vi.fn();
    const api = installDocMindApi({
      imports: {
        subscribe: vi.fn((_id, _after, callback) => {
          eventHandler = callback;
          return { requestId: job.id, cancel };
        }),
      },
    });
    const view = render(
      <AppProviders>
        <ImportProgress job={job} />
      </AppProviders>,
    );

    await waitFor(() =>
      expect(api.imports.subscribe).toHaveBeenCalledWith(job.id, 0, expect.any(Function)),
    );
    eventHandler?.({
      sequence: 2,
      payload: { progress: 65, currentStage: "索引", message: "正在索引" },
    });
    eventHandler?.({
      sequence: 1,
      payload: { progress: 10, currentStage: "解析", message: "过期事件" },
    });
    expect(await screen.findByText("正在索引")).toBeVisible();
    expect(screen.getByRole("progressbar", { name: "导入进度" })).toHaveAttribute(
      "aria-valuenow",
      "65",
    );
    view.unmount();
    expect(cancel).toHaveBeenCalledTimes(1);
  });

  it("does not let a delayed initial job snapshot overwrite newer streamed progress", async () => {
    const delayedJob = { ...job, id: "00000000-0000-0000-0000-000000000025" };
    let resolveGet: ((value: typeof delayedJob) => void) | undefined;
    let eventHandler:
      ((event: { sequence: number; payload: Record<string, unknown> }) => void) | undefined;
    const api = installDocMindApi({
      imports: {
        get: vi.fn().mockImplementation(
          () =>
            new Promise<typeof delayedJob>((resolve) => {
              resolveGet = resolve;
            }),
        ),
        subscribe: vi.fn((_id, _after, callback) => {
          eventHandler = callback;
          return { requestId: delayedJob.id, cancel: vi.fn() };
        }),
      },
    });
    render(
      <AppProviders>
        <ImportProgress job={delayedJob} />
      </AppProviders>,
    );

    expect(api.imports.subscribe).not.toHaveBeenCalled();
    resolveGet?.(delayedJob);
    await waitFor(() => expect(api.imports.subscribe).toHaveBeenCalled());
    eventHandler?.({
      sequence: 2,
      payload: { progress: 72, currentStage: "索引", message: "正在索引" },
    });
    expect(await screen.findByText("正在索引")).toBeVisible();
    expect(screen.getByRole("progressbar", { name: "导入进度" })).toHaveAttribute(
      "aria-valuenow",
      "72",
    );
  });
});
