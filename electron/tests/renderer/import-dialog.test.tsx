import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppProviders } from "../../renderer/src/app/AppProviders";
import { appQueryClient } from "../../renderer/src/app/query-client";
import { ImportDialog } from "../../renderer/src/features/imports/ImportDialog";
import { useImportStore } from "../../renderer/src/features/imports/import-store";
import { installDocMindApi, preview, repository } from "./test-docmind-api";

describe("单文档导入", () => {
  beforeEach(() => {
    appQueryClient.clear();
    useImportStore.getState().reset();
  });

  it("does not create an import before the user reaches final confirmation", async () => {
    const api = installDocMindApi({
      embedding: {
        status: vi.fn().mockResolvedValue({
          state: "ready",
          modelName: "BAAI/bge-base-zh-v1.5",
          dimension: 768,
          message: "已就绪",
          progress: 100,
        }),
      },
    });
    render(
      <AppProviders>
        <ImportDialog open onClose={() => undefined} />
      </AppProviders>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Markdown" }));
    fireEvent.click(screen.getByRole("button", { name: "选择 Markdown 文件" }));
    expect(await screen.findByText("guide.md")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "继续" }));
    await screen.findByLabelText("目标知识库");
    expect(api.imports.create).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("目标知识库"), { target: { value: repository.id } });
    fireEvent.click(screen.getByRole("button", { name: "继续" }));
    expect(await screen.findByRole("button", { name: "确认导入" })).toBeVisible();
    expect(api.imports.create).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认导入" }));
    await waitFor(() => expect(api.imports.create).toHaveBeenCalledTimes(1));
  });

  it("resets a dismissed source flow without calling create", async () => {
    const api = installDocMindApi();
    const onClose = () => undefined;
    const view = render(
      <AppProviders>
        <ImportDialog open onClose={onClose} />
      </AppProviders>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Markdown" }));
    fireEvent.click(screen.getByRole("button", { name: "选择 Markdown 文件" }));
    expect(await screen.findByText("guide.md")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "关闭导入窗口" }));
    view.rerender(
      <AppProviders>
        <ImportDialog open={false} onClose={onClose} />
      </AppProviders>,
    );
    expect(useImportStore.getState().source).toBeNull();
    expect(api.imports.create).not.toHaveBeenCalled();
  });

  it("ignores an inspection result that resolves after the dialog closes", async () => {
    let resolveInspection!: (value: typeof preview) => void;
    const inspection = new Promise<typeof preview>((resolve) => {
      resolveInspection = resolve;
    });
    installDocMindApi({
      imports: { inspect: vi.fn().mockReturnValue(inspection) },
    });
    let open = true;
    const onClose = () => {
      open = false;
      rerender(
        <AppProviders>
          <ImportDialog open={open} onClose={onClose} />
        </AppProviders>,
      );
    };
    const { rerender } = render(
      <AppProviders>
        <ImportDialog open={open} onClose={onClose} />
      </AppProviders>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Markdown" }));
    fireEvent.click(screen.getByRole("button", { name: "选择 Markdown 文件" }));
    expect(await screen.findByText("guide.md")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "继续" }));
    expect(screen.getByRole("button", { name: "正在解析…" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "关闭导入窗口" }));

    await act(async () => resolveInspection(preview));
    open = true;
    rerender(
      <AppProviders>
        <ImportDialog open={open} onClose={onClose} />
      </AppProviders>,
    );

    expect(screen.getByRole("button", { name: "Markdown" })).toBeVisible();
    expect(useImportStore.getState().preview).toBeNull();
    expect(useImportStore.getState().source).toBeNull();
  });

  it("uses an inline-created repository without waiting for a stale list refetch", async () => {
    const createdRepository = {
      ...repository,
      id: "00000000-0000-0000-0000-000000000031",
      yuqueId: "new-repository",
      name: "新知识库",
    };
    const api = installDocMindApi({
      embedding: {
        status: vi.fn().mockResolvedValue({
          state: "ready",
          modelName: "BAAI/bge-base-zh-v1.5",
          dimension: 768,
          message: "已就绪",
          progress: 100,
        }),
      },
      repositories: {
        list: vi.fn().mockResolvedValue([repository]),
        create: vi.fn().mockResolvedValue(createdRepository),
      },
    });
    render(
      <AppProviders>
        <ImportDialog open onClose={() => undefined} />
      </AppProviders>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Markdown" }));
    fireEvent.click(screen.getByRole("button", { name: "选择 Markdown 文件" }));
    expect(await screen.findByText("guide.md")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "继续" }));
    await screen.findByLabelText("新建知识库名称");
    fireEvent.change(screen.getByLabelText("新建知识库名称"), {
      target: { value: createdRepository.name },
    });
    fireEvent.click(screen.getByRole("button", { name: "新建知识库" }));
    await waitFor(() => expect(api.repositories.create).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "继续" }));

    expect(await screen.findByRole("button", { name: "确认导入" })).toBeVisible();
    expect(screen.getByText(createdRepository.name)).toBeVisible();
  });

  it("resets to step one after a successful import before reopening", async () => {
    const api = installDocMindApi({
      embedding: {
        status: vi.fn().mockResolvedValue({
          state: "ready",
          modelName: "BAAI/bge-base-zh-v1.5",
          dimension: 768,
          message: "已就绪",
          progress: 100,
        }),
      },
    });
    let open = true;
    const onClose = () => {
      open = false;
      rerender(
        <AppProviders>
          <ImportDialog onImported={onClose} open={open} onClose={onClose} />
        </AppProviders>,
      );
    };
    const { rerender } = render(
      <AppProviders>
        <ImportDialog onImported={onClose} open={open} onClose={onClose} />
      </AppProviders>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Markdown" }));
    fireEvent.click(screen.getByRole("button", { name: "选择 Markdown 文件" }));
    expect(await screen.findByText("guide.md")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "继续" }));
    await screen.findByLabelText("目标知识库");
    fireEvent.change(screen.getByLabelText("目标知识库"), { target: { value: repository.id } });
    fireEvent.click(screen.getByRole("button", { name: "继续" }));
    fireEvent.click(await screen.findByRole("button", { name: "确认导入" }));
    await waitFor(() => expect(api.imports.create).toHaveBeenCalledTimes(1));

    open = true;
    rerender(
      <AppProviders>
        <ImportDialog onImported={onClose} open={open} onClose={onClose} />
      </AppProviders>,
    );
    expect(screen.queryByRole("button", { name: "选择 Markdown 文件" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Markdown" })).toBeVisible();
    expect(api.imports.create).toHaveBeenCalledTimes(1);
  });
});
