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
          modelName: "BAAI/bge-m3",
          dimension: 1024,
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
      provider: "yuque",
      remoteId: "new-repository",
      name: "新知识库",
    };
    const api = installDocMindApi({
      embedding: {
        status: vi.fn().mockResolvedValue({
          state: "ready",
          modelName: "BAAI/bge-m3",
          dimension: 1024,
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
          modelName: "BAAI/bge-m3",
          dimension: 1024,
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

  it("lets the user confirm while the embedding model is still warming up", async () => {
    // 嵌入模型由后端在应用启动时自动预热（见 backend/app/main.py lifespan），
    // 确认页因此**不能**因为 state !== "ready" 就禁用确认按钮。
    const api = installDocMindApi({
      embedding: {
        status: vi.fn().mockResolvedValue({
          state: "unavailable",
          modelName: "BAAI/bge-m3",
          message: "模型已内置，正在加载",
          progress: null,
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
    fireEvent.change(screen.getByLabelText("目标知识库"), { target: { value: repository.id } });
    fireEvent.click(screen.getByRole("button", { name: "继续" }));

    const confirm = await screen.findByRole("button", { name: "确认导入" });
    expect(confirm).toBeEnabled();
    // 不再有任何「准备模型」按钮，也不该触发 embedding.prepare。
    expect(screen.queryByRole("button", { name: "准备模型" })).not.toBeInTheDocument();
    fireEvent.click(confirm);
    await waitFor(() => expect(api.imports.create).toHaveBeenCalledTimes(1));
    expect(api.embedding.prepare).not.toHaveBeenCalled();
  });

  it("blocks confirmation only when the embedding model actually failed to load", async () => {
    installDocMindApi({
      embedding: {
        status: vi.fn().mockResolvedValue({
          state: "error",
          modelName: "BAAI/bge-m3",
          message: "嵌入模型准备失败",
          progress: null,
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
    fireEvent.change(screen.getByLabelText("目标知识库"), { target: { value: repository.id } });
    fireEvent.click(screen.getByRole("button", { name: "继续" }));

    // 加载失败时点了确认也只会拿到 503，所以这里必须挡住并给出原因。
    expect(await screen.findByRole("alert")).toHaveTextContent("嵌入模型准备失败");
    expect(screen.getByRole("button", { name: "确认导入" })).toBeDisabled();
  });
});
