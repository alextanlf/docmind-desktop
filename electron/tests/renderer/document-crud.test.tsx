import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { AppProviders } from "../../renderer/src/app/AppProviders";
import { appQueryClient } from "../../renderer/src/app/query-client";
import { ArchiveDocumentsDialog } from "../../renderer/src/features/repositories/ArchiveDocumentsDialog";
import { DocumentEditor } from "../../renderer/src/features/repositories/DocumentEditor";
import { document, installDocMindApi, repository, source } from "./test-docmind-api";

describe("文档编辑", () => {
  beforeEach(() => appQueryClient.clear());

  it("archives without asking the title back, because archiving is undoable", () => {
    installDocMindApi();
    render(
      <ArchiveDocumentsDialog
        documents={[document]}
        error=""
        onClose={() => undefined}
        onConfirm={() => undefined}
        pending={false}
        repositoryName={repository.name}
      />,
    );

    // 摩擦与不可逆性成比例：归档可以在设置页一键恢复，所以这里不该有一道
    // "把标题一字不差打一遍"的门。
    expect(screen.queryByLabelText("输入文档标题以确认")).toBeNull();
    expect(screen.getByRole("button", { name: "归档" })).toBeEnabled();
  });

  it("names the repository and says where the archived document can be restored", () => {
    installDocMindApi();
    render(
      <ArchiveDocumentsDialog
        documents={[document]}
        error=""
        onClose={() => undefined}
        onConfirm={() => undefined}
        pending={false}
        repositoryName={repository.name}
      />,
    );

    expect(screen.getByText(new RegExp(`将从「${repository.name}」归档`))).toBeVisible();
    expect(screen.getByText(/设置 →\s*已归档的文档/)).toBeVisible();
  });

  it("renders the document as formatted content instead of raw Markdown", () => {
    installDocMindApi();
    render(
      <AppProviders>
        <DocumentEditor document={document} onClose={() => undefined} />
      </AppProviders>,
    );

    const view = screen.getByLabelText("文档内容");
    expect(view.querySelector("h1")).toHaveTextContent("State 管理");
    expect(view).not.toHaveTextContent("# State 管理");
    expect(screen.queryByLabelText("Markdown 内容")).not.toBeInTheDocument();
  });

  it("reveals the raw Markdown source only after switching to edit mode", () => {
    installDocMindApi();
    render(
      <AppProviders>
        <DocumentEditor document={document} onClose={() => undefined} />
      </AppProviders>,
    );

    fireEvent.click(screen.getByRole("button", { name: "编辑文档" }));
    expect(screen.getByLabelText("Markdown 内容")).toHaveValue(document.content);

    fireEvent.click(screen.getByRole("button", { name: "预览文档" }));
    expect(screen.queryByLabelText("Markdown 内容")).not.toBeInTheDocument();
  });

  it("discards unsaved edits when leaving edit mode", () => {
    installDocMindApi();
    render(
      <AppProviders>
        <DocumentEditor document={document} onClose={() => undefined} />
      </AppProviders>,
    );

    fireEvent.click(screen.getByRole("button", { name: "编辑文档" }));
    fireEvent.change(screen.getByLabelText("Markdown 内容"), { target: { value: "# 草稿" } });
    fireEvent.click(screen.getByRole("button", { name: "预览文档" }));

    expect(screen.getByLabelText("文档内容")).toHaveTextContent("State 管理");
    expect(screen.getByLabelText("文档内容")).not.toHaveTextContent("草稿");
  });

  it("saves Markdown edits and refreshes the repository documents", async () => {
    const api = installDocMindApi();
    render(
      <AppProviders>
        <DocumentEditor document={document} onClose={() => undefined} />
      </AppProviders>,
    );

    fireEvent.click(screen.getByRole("button", { name: "编辑文档" }));
    fireEvent.change(screen.getByLabelText("文档标题"), { target: { value: "更新后的 State" } });
    fireEvent.change(screen.getByLabelText("Markdown 内容"), { target: { value: "# 新内容" } });
    fireEvent.click(screen.getByRole("button", { name: "保存文档" }));

    await waitFor(() =>
      expect(api.documents.update).toHaveBeenCalledWith(document.id, {
        title: "更新后的 State",
        content: "# 新内容",
      }),
    );
    await waitFor(() => expect(screen.getByLabelText("文档内容")).toHaveTextContent("新内容"));
  });
});

/**
 * 原件的三种状态在界面上的表现。
 *
 * 这一组守的是"后端说得出、界面看得见"：后端从阶段 2 起就能表达"原件被改了/丢了"，
 * 但如果没有这一层，那句话永远到不了用户眼前 —— 他会一直以为正文是最新的。
 */
describe("原件状态", () => {
  beforeEach(() => appQueryClient.clear());

  function renderWith(state: "available" | "changed" | "missing" | "none") {
    const api = installDocMindApi();
    const instance = { ...document, originalState: state };
    render(
      <AppProviders>
        <DocumentEditor document={instance} onClose={() => undefined} />
      </AppProviders>,
    );
    return api;
  }

  it("says nothing when the original matches and when there is no original", () => {
    for (const state of ["available", "none"] as const) {
      const { unmount } = render(
        <AppProviders>
          <DocumentEditor
            document={{ ...document, originalState: state }}
            onClose={() => undefined}
          />
        </AppProviders>,
      );
      expect(screen.queryByText(/原件/)).not.toBeInTheDocument();
      unmount();
    }
  });

  it("asks before reconvert overwrites edits, and only then reconverts", async () => {
    const api = renderWith("changed");

    expect(screen.getByText(/原件已经变过/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /重新转换/ }));

    // 先问一句：重新转换整篇替换正文，而"原件已更新"本身就在鼓励用户去原件里改。
    expect(await screen.findByText(/替换「/)).toBeInTheDocument();
    // **确认之前一个字节都不许动。** 只断言"弹窗出现了"会漏掉"其实已经转换了"。
    expect(api.documents.reconvert).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "覆盖并重新转换" }));

    await waitFor(() => expect(api.documents.reconvert).toHaveBeenCalledWith(document.id));
  });

  it("does nothing when the reconvert confirmation is cancelled", async () => {
    const api = renderWith("changed");

    fireEvent.click(screen.getByRole("button", { name: /重新转换/ }));
    fireEvent.click(await screen.findByRole("button", { name: "取消" }));

    expect(screen.queryByText(/替换「/)).not.toBeInTheDocument();
    expect(api.documents.reconvert).not.toHaveBeenCalled();
  });

  it("opens the original with the system program, sending only the document id", async () => {
    const api = renderWith("available");

    // 渲染层只发 documentId：路径由主进程向后端要、验签之后才落到文件系统上。
    fireEvent.click(screen.getByRole("button", { name: "用系统程序打开原件" }));

    await waitFor(() => expect(api.documents.openOriginal).toHaveBeenCalledWith(document.id));
  });

  it("does not offer to open an original that is gone", () => {
    renderWith("missing");

    // 原件不在原处时，出口是「重新定位原件」而不是"打开"（打开只会失败）。
    expect(screen.queryByRole("button", { name: "用系统程序打开原件" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /重新定位原件/ })).toBeInTheDocument();
  });

  it("offers only relink when the original is gone", () => {
    renderWith("missing");

    expect(screen.getByText(/找不到原件/)).toBeInTheDocument();
    // 「重新转换」在没有原件时毫无意义 —— 它只会立刻失败。
    expect(screen.queryByRole("button", { name: /重新转换/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /重新定位原件/ })).toBeInTheDocument();
  });

  it("opens the picker and rebinds to whatever the user chose", async () => {
    const api = installDocMindApi();
    render(
      <AppProviders>
        <DocumentEditor
          document={{ ...document, originalState: "missing" }}
          onClose={() => undefined}
        />
      </AppProviders>,
    );

    fireEvent.click(screen.getByRole("button", { name: /重新定位原件/ }));

    await waitFor(() =>
      expect(api.documents.relinkOriginal).toHaveBeenCalledWith(document.id, source.path),
    );
  });
});
