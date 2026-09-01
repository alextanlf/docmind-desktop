import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Component, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ErrorBoundary } from "../../renderer/src/app/ErrorBoundary";
import { AppProviders } from "../../renderer/src/app/AppProviders";
import { appQueryClient } from "../../renderer/src/app/query-client";
import { Workspace } from "../../renderer/src/app/Workspace";
import { repositoryKeys } from "../../renderer/src/features/repositories/repository.queries";
import { useUiStore } from "../../renderer/src/stores/ui-store";
import { document, installDocMindApi, repository } from "./test-docmind-api";

class BrokenView extends Component {
  render(): ReactNode {
    throw new Error("secret stack details");
  }
}

function renderWorkspace() {
  return render(
    <AppProviders>
      <Workspace />
    </AppProviders>,
  );
}

describe("Workspace", () => {
  beforeEach(() => {
    installDocMindApi();
    useUiStore.setState({
      activeView: "workspace",
      sidebarCollapsed: false,
      referencePanelOpen: true,
      activeCitationId: null,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("renders stable three-column workspace dimensions and responsive classes", () => {
    renderWorkspace();

    expect(screen.getByLabelText("工作台")).toHaveClass("workspace-grid");
    expect(screen.getByLabelText("主导航")).toHaveClass("w-[248px]");
    expect(screen.getByLabelText("主导航")).toHaveClass("workspace-sidebar");
    expect(screen.getByLabelText("引用资料")).toHaveClass("w-[320px]");
    expect(screen.getByLabelText("引用资料")).toHaveClass("workspace-reference");
  });

  it("supports keyboard sidebar controls with accessible icon labels", () => {
    renderWorkspace();

    const collapse = screen.getByRole("button", { name: "收起侧边栏" });
    collapse.focus();
    fireEvent.keyDown(collapse, { key: "Enter" });
    fireEvent.click(collapse);

    expect(screen.getByLabelText("主导航")).toHaveClass("is-collapsed");
    expect(screen.getByRole("button", { name: "展开侧边栏" })).toHaveAttribute(
      "title",
      "展开侧边栏",
    );
  });

  it("keeps rail navigation named and titled when sidebar labels are hidden", () => {
    useUiStore.setState({ sidebarCollapsed: true });
    renderWorkspace();

    for (const label of ["新建会话", "知识库", "导入文档", "设置"]) {
      const button = screen.getByRole("button", { name: label });
      expect(button).toHaveAttribute("aria-label", label);
      expect(button).toHaveAttribute("title", label);
    }
  });

  it("reopens the reference panel after it is closed", () => {
    renderWorkspace();

    fireEvent.click(screen.getByRole("button", { name: "关闭引用资料" }));
    expect(screen.getByLabelText("工作台")).toHaveClass("reference-is-closed");
    const reopen = screen.getByRole("button", { name: "打开引用资料" });
    fireEvent.click(reopen);

    expect(screen.getByLabelText("工作台")).not.toHaveClass("reference-is-closed");
    expect(screen.queryByRole("button", { name: "打开引用资料" })).not.toBeInTheDocument();
  });

  it("removes the unavailable collapse action when the viewport forces the icon rail", () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockReturnValue({
        matches: true,
        media: "(max-width: 1000px)",
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      }),
    );
    renderWorkspace();

    expect(screen.queryByRole("button", { name: "收起侧边栏" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "展开侧边栏" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "设置" })).toBeVisible();
  });

  it("transfers focus when the forced icon rail removes the focused collapse action", async () => {
    let changeListener: ((event: MediaQueryListEvent) => void) | undefined;
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockReturnValue({
        matches: false,
        media: "(max-width: 1000px)",
        onchange: null,
        addEventListener: vi.fn((event: string, listener: (event: MediaQueryListEvent) => void) => {
          if (event === "change") changeListener = listener;
        }),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      }),
    );
    renderWorkspace();
    const collapse = screen.getByRole("button", { name: "收起侧边栏" });
    collapse.focus();

    act(() => changeListener?.({ matches: true } as MediaQueryListEvent));

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "收起侧边栏" })).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "导入文档" })).toHaveFocus();
  });

  it("names the actual repository in the document delete confirmation", async () => {
    installDocMindApi();
    renderWorkspace();

    fireEvent.click(await screen.findByRole("button", { name: `展开 ${repository.name}` }));
    fireEvent.click(await screen.findByRole("button", { name: document.title }));
    fireEvent.click(await screen.findByRole("button", { name: "删除文档" }));

    expect(
      await screen.findByText(new RegExp(`将从「${repository.name}」删除「${document.title}」`)),
    ).toBeVisible();
  });

  it("saves the selected cached document with its own draft after switching documents", async () => {
    const secondDocument = {
      ...document,
      id: "00000000-0000-0000-0000-000000000023",
      yuqueId: "navigation",
      title: "Navigation",
      content: "# Navigation\n\nSecond document content",
    };
    appQueryClient.clear();
    appQueryClient.setQueryData(repositoryKeys.document(document.id), document);
    appQueryClient.setQueryData(repositoryKeys.document(secondDocument.id), secondDocument);
    const api = installDocMindApi({
      documents: {
        list: vi.fn().mockResolvedValue([document, secondDocument]),
      },
    });
    renderWorkspace();

    fireEvent.click(await screen.findByRole("button", { name: `展开 ${repository.name}` }));
    fireEvent.click(await screen.findByRole("button", { name: document.title }));
    fireEvent.change(await screen.findByLabelText("Markdown 内容"), {
      target: { value: "# Unsaved first document draft" },
    });
    fireEvent.click(screen.getByRole("button", { name: secondDocument.title }));

    expect(await screen.findByLabelText("文档标题")).toHaveValue(secondDocument.title);
    expect(screen.getByLabelText("Markdown 内容")).toHaveValue(secondDocument.content);
    fireEvent.click(screen.getByRole("button", { name: "保存文档" }));
    await waitFor(() =>
      expect(api.documents.update).toHaveBeenCalledWith(secondDocument.id, {
        title: secondDocument.title,
        content: secondDocument.content,
      }),
    );
  });

  it("shows a safe recovery view without exposing the error stack", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    render(
      <ErrorBoundary>
        <BrokenView />
      </ErrorBoundary>,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("应用界面发生错误");
    expect(screen.getByRole("button", { name: "重新加载应用" })).toBeVisible();
    expect(screen.queryByText(/secret stack details/i)).not.toBeInTheDocument();
  });
});
