import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Component, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ErrorBoundary } from "../../renderer/src/app/ErrorBoundary";
import { AppProviders } from "../../renderer/src/app/AppProviders";
import { appQueryClient } from "../../renderer/src/app/query-client";
import { Workspace } from "../../renderer/src/app/Workspace";
import { repositoryKeys } from "../../renderer/src/features/repositories/repository.queries";
import { useUiStore } from "../../renderer/src/stores/ui-store";
import { document, installDocMindApi, repository, session } from "./test-docmind-api";

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

  it("keeps the reference panel out of the empty home view", () => {
    renderWorkspace();

    const workspace = screen.getByLabelText("工作台");
    expect(workspace).toHaveClass("workspace-grid");
    expect(workspace).toHaveClass("reference-is-closed");
    expect(workspace.style.getPropertyValue("--sidebar-width")).toBe("248px");
    expect(workspace.style.getPropertyValue("--reference-width")).toBe("0px");
    expect(screen.getByLabelText("主导航")).toHaveClass("workspace-sidebar");
    expect(screen.queryByLabelText("引用资料")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "打开引用资料" })).not.toBeInTheDocument();
  });

  it("restores the reference panel for a session and for an open document", async () => {
    renderWorkspace();

    fireEvent.click(await screen.findByRole("button", { name: session.title }));

    const workspace = screen.getByLabelText("工作台");
    expect(workspace).not.toHaveClass("reference-is-closed");
    expect(screen.getByLabelText("引用资料")).toHaveClass("workspace-reference");
    expect(workspace.style.getPropertyValue("--reference-width")).toBe("320px");

    fireEvent.click(screen.getByRole("button", { name: `展开 ${repository.name}` }));
    fireEvent.click(await screen.findByRole("button", { name: document.title }));

    expect(workspace).not.toHaveClass("reference-is-closed");
    expect(screen.getByLabelText("引用资料")).toBeInTheDocument();
  });

  it("hides the reference panel again on the settings and memory views", async () => {
    renderWorkspace();

    fireEvent.click(await screen.findByRole("button", { name: session.title }));
    expect(screen.getByLabelText("引用资料")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "记忆" }));
    expect(screen.queryByLabelText("引用资料")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "设置" }));
    expect(screen.queryByLabelText("引用资料")).not.toBeInTheDocument();
  });

  it("pins settings to the bottom-left of the sidebar, outside the feature nav", async () => {
    renderWorkspace();

    // The nav holds the three things you *work with*; settings is a destination
    // pinned below the library tree, so it must not read as a fourth peer.
    const nav = screen.getByRole("navigation", { name: "功能导航" });
    expect(within(nav).queryByRole("button", { name: "设置" })).not.toBeInTheDocument();

    const sidebar = screen.getByLabelText("主导航");
    const settingsNav = within(sidebar).getByRole("button", { name: "设置" });
    // Bottom-left means the last thing in the sidebar column, after the tree.
    expect(sidebar.lastElementChild).toBe(settingsNav);
    expect(settingsNav).not.toHaveAttribute("aria-current");

    fireEvent.click(settingsNav);
    expect(await screen.findByRole("heading", { name: "设置", level: 1 })).toBeVisible();
    expect(settingsNav).toHaveAttribute("aria-current", "page");

    // Pinned position must not reintroduce the old toggle: clicking again has
    // to keep you on the settings page instead of bouncing back to the
    // conversation, which is what made it read as a mode you could fall out of.
    fireEvent.click(settingsNav);
    expect(screen.getByRole("heading", { name: "设置", level: 1 })).toBeVisible();
    expect(screen.getByLabelText("设置内容")).toBeInTheDocument();
    expect(screen.queryByLabelText("对话工作区")).not.toBeInTheDocument();
  });

  it("switches cleanly from settings back to a workspace page", async () => {
    renderWorkspace();

    fireEvent.click(screen.getByRole("button", { name: "设置" }));
    await screen.findByRole("heading", { name: "设置", level: 1 });

    // Scoped to the nav: "记忆" names both the nav button and the page section
    // it opens, so an unscoped query would be ambiguous by design.
    const nav = screen.getByRole("navigation", { name: "功能导航" });
    fireEvent.click(within(nav).getByRole("button", { name: "记忆" }));

    // Leaving settings is an explicit choice, so the previous page must be gone
    // and the memory page shown instead.
    expect(screen.queryByRole("heading", { name: "设置", level: 1 })).not.toBeInTheDocument();
    expect(await screen.findByRole("region", { name: "记忆" })).toBeInTheDocument();
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

  it("reopens the reference panel after it is closed", async () => {
    renderWorkspace();

    fireEvent.click(await screen.findByRole("button", { name: session.title }));
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
      remoteId: "navigation",
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
    fireEvent.click(screen.getByRole("button", { name: "编辑文档" }));
    fireEvent.change(await screen.findByLabelText("Markdown 内容"), {
      target: { value: "# Unsaved first document draft" },
    });
    fireEvent.click(screen.getByRole("button", { name: secondDocument.title }));

    // 正文里也有同名标题，只认编辑器 header 上的这一个。
    const editor = await screen.findByLabelText("文档编辑器");
    await waitFor(() =>
      expect(editor.querySelector(".document-view-title")).toHaveTextContent(secondDocument.title),
    );
    fireEvent.click(screen.getByRole("button", { name: "编辑文档" }));
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
