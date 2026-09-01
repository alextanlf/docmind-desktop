import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MarkdownMessage } from "../../renderer/src/features/chat/MarkdownMessage";
import { ReferencePanel } from "../../renderer/src/features/references/ReferencePanel";
import { useUiStore } from "../../renderer/src/stores/ui-store";
import { citation, installDocMindApi } from "./test-docmind-api";

function ReferenceSurface({ content = "答案 [S1]" }: { content?: string }) {
  return (
    <>
      <MarkdownMessage citationScope="message-1" content={content} citations={[citation]} />
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
    useUiStore.setState({ activeCitationId: "yuque", referencePanelOpen: true });
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

  it("uses the same URL policy for Markdown and reference sources", () => {
    const api = installDocMindApi();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const unsupported = { ...citation, sourceUrl: "mailto:security@example.com" };
    const yuque = { ...citation, sourceUrl: "https://www.yuque.com/test/swiftui/state" };
    useUiStore.setState({ activeCitationId: "yuque", referencePanelOpen: true });
    render(
      <>
        <MarkdownMessage
          citationScope="message-1"
          content="[脚本](javascript:alert(1)) [外部](https://example.com) [语雀](https://www.yuque.com/test)"
          citations={[]}
        />
        <ReferencePanel
          citations={[
            { id: "unsupported", citation: unsupported },
            { id: "yuque", citation: yuque },
          ]}
        />
      </>,
    );

    expect(screen.queryByRole("link", { name: "脚本" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "外部" }));
    expect(api.shell.openExternal).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("link", { name: "语雀" }));
    expect(api.shell.openExternal).toHaveBeenCalledWith("https://www.yuque.com/test");

    fireEvent.click(screen.getByRole("button", { name: "打开原始来源" }));
    expect(api.shell.openExternal).toHaveBeenCalledTimes(2);
    confirm.mockRestore();
  });

  it("copies plain fenced code and keeps long reference content scrollable above its footer", () => {
    render(<ReferenceSurface content={"答案 [S1]\n\n```\nconst state = 1;\n```"} />);
    const copy = screen.getByRole("button", { name: "复制代码" });
    expect(copy).toHaveAttribute("title", "复制代码");

    fireEvent.click(screen.getByRole("button", { name: "查看引用 S1" }));
    expect(screen.getByRole("button", { name: "打开原始来源" }).parentElement).toHaveClass(
      "reference-footer",
    );
  });

  it("keeps repeated source IDs scoped to the triggering answer and restores focus there", () => {
    const newer = {
      ...citation,
      title: "更新后的状态管理",
      sectionPath: "更新 > @State",
      chunkId: "chunk-state-2",
    };
    render(
      <>
        <MarkdownMessage citationScope="message-1" content="旧答案 [S1]" citations={[citation]} />
        <MarkdownMessage citationScope="message-2" content="新答案 [S1]" citations={[newer]} />
        <ReferencePanel citations={[citation, newer] as any} />
      </>,
    );
    const controls = screen.getAllByRole("button", { name: "查看引用 S1" });

    fireEvent.click(controls[0]);
    expect(screen.getByLabelText("引用资料内容")).toHaveTextContent(citation.title);
    fireEvent.click(controls[1]);
    expect(screen.getByLabelText("引用资料内容")).toHaveTextContent(newer.title);
    fireEvent.click(screen.getByRole("button", { name: "关闭引用资料" }));

    expect(controls[1]).toHaveFocus();
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
