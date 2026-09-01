import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MarkdownMessage } from "../../renderer/src/features/chat/MarkdownMessage";
import { ReferencePanel } from "../../renderer/src/features/references/ReferencePanel";
import { useUiStore } from "../../renderer/src/stores/ui-store";
import { citation, installDocMindApi } from "./test-docmind-api";

function ReferenceSurface({ content = "答案 [S1]" }: { content?: string }) {
  return (
    <>
      <MarkdownMessage content={content} citations={[citation]} />
      <aside className="workspace-reference">
        <ReferencePanel citations={[citation]} />
      </aside>
    </>
  );
}

describe("引用资料", () => {
  beforeEach(() => {
    installDocMindApi();
    useUiStore.setState({ activeCitationId: null, referencePanelOpen: false });
  });

  it("opens a citation in the reference panel without layout overlap", async () => {
    render(<ReferenceSurface />);

    fireEvent.click(screen.getByRole("button", { name: "查看引用 S1" }));

    expect(screen.getByLabelText("引用资料内容")).toHaveTextContent("状态管理 > @State");
    expect(screen.getByRole("button", { name: "打开原始来源" })).toBeVisible();
  });

  it("asks before opening non-Yuque links and copies code blocks", async () => {
    const api = installDocMindApi();
    const confirm = vi
      .spyOn(window, "confirm")
      .mockReturnValueOnce(false)
      .mockReturnValueOnce(true);
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(
      <ReferenceSurface content={"[外部](https://example.com)\n\n```ts\nconst state = 1;\n```"} />,
    );

    fireEvent.click(screen.getByRole("link", { name: "外部" }));
    expect(api.shell.openExternal).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("link", { name: "外部" }));
    expect(api.shell.openExternal).toHaveBeenCalledWith("https://example.com");
    fireEvent.click(screen.getByRole("button", { name: "复制代码" }));
    expect(writeText).toHaveBeenCalledWith("const state = 1;");
    confirm.mockRestore();
  });

  it("returns focus to the citation after closing the narrow reference drawer", async () => {
    const originalMatchMedia = window.matchMedia;
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: () => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }),
    });
    render(<ReferenceSurface />);
    const citationButton = screen.getByRole("button", { name: "查看引用 S1" });

    fireEvent.click(citationButton);
    fireEvent.click(screen.getByRole("button", { name: "关闭引用资料" }));

    expect(citationButton).toHaveFocus();
    Object.defineProperty(window, "matchMedia", { configurable: true, value: originalMatchMedia });
  });
});
