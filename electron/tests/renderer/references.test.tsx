import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MarkdownMessage } from "../../renderer/src/features/chat/MarkdownMessage";
import { ReferencePanel } from "../../renderer/src/features/references/ReferencePanel";
import { useUiStore } from "../../renderer/src/stores/ui-store";
import { citation, installDocMindApi } from "./test-docmind-api";

function ReferenceSurface({
  content = "答案 [S1]",
  messageKey,
}: {
  content?: string;
  messageKey?: string;
}) {
  return (
    <>
      <MarkdownMessage
        key={messageKey}
        citationScope="message-1"
        content={content}
        citations={[citation]}
      />
      <aside className="workspace-reference">
        <ReferencePanel citations={[citation]} />
      </aside>
    </>
  );
}

function ReferenceOpenMarker() {
  const open = useUiStore((state) => state.referencePanelOpen);

  return <output data-open={String(open)} data-testid="reference-open-state" />;
}

describe("引用资料", () => {
  beforeEach(() => {
    installDocMindApi();
    useUiStore.setState({
      activeCitationId: null,
      activeCitationTrigger: null,
      referencePanelOpen: false,
    });
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

  it("keeps inline code inline without a block copy action", () => {
    render(<ReferenceSurface content={"使用 `@State` 管理状态。"} />);

    expect(screen.getByText("@State").tagName).toBe("CODE");
    expect(screen.queryByRole("button", { name: "复制代码" })).not.toBeInTheDocument();
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
    render(
      <>
        <ReferenceSurface />
        <ReferenceOpenMarker />
      </>,
    );
    const citationButton = screen.getByRole("button", { name: "查看引用 S1" });
    const openState = screen.getByTestId("reference-open-state");
    const focusStates: string[] = [];
    citationButton.addEventListener("focus", () => focusStates.push(openState.dataset.open ?? ""));

    fireEvent.click(citationButton);
    expect(useUiStore.getState().activeCitationTrigger).toBe(citationButton);
    fireEvent.click(screen.getByRole("button", { name: "关闭引用资料" }));

    await waitFor(() => expect(openState).toHaveAttribute("data-open", "false"));
    expect(focusStates).toEqual(["false"]);
    expect(citationButton).toHaveFocus();
    Object.defineProperty(window, "matchMedia", { configurable: true, value: originalMatchMedia });
  });

  it("returns focus to the current citation trigger after the original trigger remounts", async () => {
    const { rerender } = render(
      <>
        <ReferenceSurface messageKey="original" />
        <ReferenceOpenMarker />
      </>,
    );
    const originalTrigger = screen.getByRole("button", { name: "查看引用 S1" });

    fireEvent.click(originalTrigger);
    rerender(
      <>
        <ReferenceSurface messageKey="replacement" />
        <ReferenceOpenMarker />
      </>,
    );
    const currentTrigger = screen.getByRole("button", { name: "查看引用 S1" });

    expect(originalTrigger.isConnected).toBe(false);
    expect(currentTrigger.isConnected).toBe(true);
    expect(currentTrigger).not.toBe(originalTrigger);
    expect(currentTrigger.dataset.citationId).toBe(originalTrigger.dataset.citationId);
    expect(useUiStore.getState().activeCitationTrigger).toBe(originalTrigger);

    fireEvent.click(screen.getByRole("button", { name: "关闭引用资料" }));

    await waitFor(() => expect(currentTrigger).toHaveFocus());
    expect(useUiStore.getState()).toMatchObject({
      activeCitationId: null,
      activeCitationTrigger: null,
      referencePanelOpen: false,
    });
  });

  it("returns focus to the activating repeated citation occurrence after remount", async () => {
    const content = "第一次引用 [S1]，第二次引用 [S1]。";
    const { rerender } = render(<ReferenceSurface content={content} messageKey="original" />);
    const originalTriggers = screen.getAllByRole("button", { name: "查看引用 S1" });

    fireEvent.click(originalTriggers[1]);
    rerender(<ReferenceSurface content={content} messageKey="replacement" />);
    const currentTriggers = screen.getAllByRole("button", { name: "查看引用 S1" });
    fireEvent.click(screen.getByRole("button", { name: "关闭引用资料" }));

    await waitFor(() => expect(currentTriggers[1]).toHaveFocus());
    expect(currentTriggers[0].dataset.citationTriggerId).not.toBe(
      currentTriggers[1].dataset.citationTriggerId,
    );
    expect(currentTriggers[1].dataset.citationTriggerId).toBe(
      originalTriggers[1].dataset.citationTriggerId,
    );
  });
});
