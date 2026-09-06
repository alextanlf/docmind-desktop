import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { parse } from "postcss";
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AppProviders } from "../../renderer/src/app/AppProviders";
import { Workspace } from "../../renderer/src/app/Workspace";
import { appQueryClient } from "../../renderer/src/app/query-client";
import { useImportStore } from "../../renderer/src/features/imports/import-store";
import { useChatStreamStore } from "../../renderer/src/stores/chat-stream-store";
import { useUiStore } from "../../renderer/src/stores/ui-store";
import { installDocMindApi, job, repository, session } from "./test-docmind-api";

beforeEach(() => {
  appQueryClient.clear();
  useImportStore.getState().reset();
  useChatStreamStore.getState().reset();
  useUiStore.setState({ activeView: "workspace", referencePanelOpen: false });
});

afterEach(() => {
  appQueryClient.clear();
  useImportStore.getState().reset();
  useChatStreamStore.getState().reset();
});

it("reserves an in-flow, height-bounded import row at every breakpoint", () => {
  const styles = readFileSync("electron/renderer/src/styles.css", "utf8");
  const stylesheet = parse(styles);
  const declarations = (selector: string) => {
    const properties: Record<string, string> = {};
    stylesheet.walkRules(selector, (rule) => {
      rule.walkDecls((declaration) => {
        properties[declaration.prop] = declaration.value;
      });
    });
    return properties;
  };
  expect(declarations(".workspace-main")).toMatchObject({
    display: "grid",
    "grid-template-rows": "minmax(0, 1fr) auto",
    overflow: "hidden",
  });
  expect(declarations(".workspace-content")).toMatchObject({ "min-height": "0", overflow: "auto" });
  expect(declarations(".workspace-import-progress")).toMatchObject({
    position: "static",
    "max-height": "min(30vh, 240px)",
    "overflow-y": "auto",
    "grid-template-columns": "repeat(auto-fit, minmax(min(100%, 280px), 1fr))",
  });
  stylesheet.walkRules(".workspace-import-progress", (rule) => {
    rule.walkDecls("position", (declaration) => expect(declaration.value).toBe("static"));
    rule.walkDecls("width", (declaration) => expect(declaration.value).toBe("auto"));
  });
  expect(declarations(".import-progress-actions")["flex-wrap"]).toBe("wrap");
});

it.each(["single", "batch", "both"])(
  "keeps %s progress in its own row while sending a message",
  async (kind) => {
    const batchId = "00000000-0000-0000-0000-000000000101";
    const api = installDocMindApi({
      imports: {
        get: vi
          .fn()
          .mockResolvedValue({ ...job, state: "completed", progress: 100, message: "导入完成" }),
      },
      batches: {
        get: vi.fn().mockResolvedValue({
          id: batchId,
          state: "running",
          progress: 50,
          message: "批量导入中",
          completedCount: 1,
          selectedCount: 2,
          failedCount: 0,
          skippedCount: 0,
          lastEventSequence: 1,
        }),
      },
      chat: {
        stream: vi.fn().mockReturnValue({ requestId: "chat", cancel: vi.fn(), detach: vi.fn() }),
      },
    });
    useImportStore.setState({
      jobId: kind === "batch" ? null : job.id,
      batchId: kind === "single" ? null : batchId,
    });
    render(
      <AppProviders>
        <Workspace />
      </AppProviders>,
    );
    fireEvent.click(await screen.findByRole("button", { name: session.title }));
    const workspace = screen.getByRole("region", { name: "对话工作区" });
    const tray = await screen.findByRole("region", { name: "导入任务" });
    expect(tray.parentElement).toBe(workspace);
    expect(workspace.querySelectorAll(".workspace-import-progress")).toHaveLength(1);
    if (kind !== "batch")
      expect(await within(tray).findByRole("region", { name: "导入进度" })).toBeInTheDocument();
    if (kind !== "single")
      expect(await within(tray).findByRole("region", { name: "批量导入进度" })).toBeInTheDocument();
    const send = screen.getByRole("button", { name: "发送消息" });
    expect(send.closest(".workspace-content")?.parentElement).toBe(workspace);
    expect(tray).not.toContainElement(send);
    fireEvent.change(screen.getByRole("textbox", { name: "输入问题" }), {
      target: { value: "导入文档讲了什么？" },
    });
    fireEvent.click(send);
    await waitFor(() =>
      expect(api.chat.stream).toHaveBeenCalledWith(
        expect.objectContaining({
          sessionId: session.id,
          message: "导入文档讲了什么？",
          repositoryIds: [repository.id],
        }),
        expect.any(Function),
      ),
    );
  },
);

it("does not reserve a task region when there are no imports", () => {
  installDocMindApi();
  render(
    <AppProviders>
      <Workspace />
    </AppProviders>,
  );
  expect(screen.queryByRole("region", { name: "导入任务" })).not.toBeInTheDocument();
});
