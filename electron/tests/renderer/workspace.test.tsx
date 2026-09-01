import { fireEvent, render, screen } from "@testing-library/react";
import { Component, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ErrorBoundary } from "../../renderer/src/app/ErrorBoundary";
import { Workspace } from "../../renderer/src/app/Workspace";
import { useUiStore } from "../../renderer/src/stores/ui-store";
import { installDocMindApi } from "./test-docmind-api";

class BrokenView extends Component {
  render(): ReactNode {
    throw new Error("secret stack details");
  }
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

  afterEach(() => vi.restoreAllMocks());

  it("renders stable three-column workspace dimensions and responsive classes", () => {
    render(<Workspace />);

    expect(screen.getByLabelText("工作台")).toHaveClass("workspace-grid");
    expect(screen.getByLabelText("主导航")).toHaveClass("w-[248px]");
    expect(screen.getByLabelText("主导航")).toHaveClass("workspace-sidebar");
    expect(screen.getByLabelText("引用资料")).toHaveClass("w-[320px]");
    expect(screen.getByLabelText("引用资料")).toHaveClass("workspace-reference");
  });

  it("supports keyboard sidebar controls with accessible icon labels", () => {
    render(<Workspace />);

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
    render(<Workspace />);

    for (const label of ["新建会话", "知识库", "导入文档", "设置"]) {
      const button = screen.getByRole("button", { name: label });
      expect(button).toHaveAttribute("aria-label", label);
      expect(button).toHaveAttribute("title", label);
    }
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
